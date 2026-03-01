import os
import pandas as pd
import datetime
import random
import math
import requests
import json
import logging
from logging.handlers import RotatingFileHandler
from flask import Flask, render_template, request, session, jsonify, redirect, url_for, flash, send_file, abort
from flask_sqlalchemy import SQLAlchemy
from flask_mail import Mail, Message
from flask_wtf.csrf import CSRFProtect
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask import send_file, abort
from werkzeug.security import generate_password_hash, check_password_hash
from dotenv import load_dotenv
from sqlalchemy import text

# Import do módulo Mercado Pago
from mercado_pago import MercadoPagoIntegration

load_dotenv()

# ============================================================
# CONFIGURAÇÃO DE AMBIENTE
# ============================================================
base_dir = os.path.abspath(os.path.dirname(__file__))

app = Flask(__name__, static_folder='static', template_folder='templates')
app.secret_key = os.environ.get('SECRET_KEY', 'chave_secreta_cnc_imports')

# Configuração de logs
if not app.debug:
    handler = RotatingFileHandler('cnc_imports.log', maxBytes=10000, backupCount=1)
    handler.setLevel(logging.INFO)
    app.logger.addHandler(handler)

# CSRF Protection
csrf = CSRFProtect(app)

# Rate Limiting
limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=["200 per day", "50 per hour"],
    storage_uri="memory://",
)

# ============================================================
# BANCO DE DADOS
# ============================================================
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get(
    'DATABASE_URL',
    'sqlite:///' + os.path.join(base_dir, 'cnc_imports.db')
)
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# ============================================================
# CONFIGURAÇÃO DE E-MAIL
# ============================================================
app.config['MAIL_SERVER'] = 'smtp.mail.yahoo.com'
app.config['MAIL_PORT'] = 465
app.config['MAIL_USE_SSL'] = True
app.config['MAIL_USERNAME'] = os.environ.get('MAIL_USERNAME', 'wendreis@yahoo.com.br')
app.config['MAIL_PASSWORD'] = os.environ.get('MAIL_PASSWORD', 'irijeyyrgtzhgvhl')
app.config['MAIL_DEFAULT_SENDER'] = app.config['MAIL_USERNAME']

db = SQLAlchemy(app)
mail = Mail(app)

# Inicializa a integração com Mercado Pago
mp_integration = MercadoPagoIntegration()


# ============================================================
# MODELOS
# ============================================================
class CEPCache(db.Model):
    __tablename__ = 'cep_cache'
    cep = db.Column(db.String(9), primary_key=True)  # formato 00000-000
    lat = db.Column(db.Float, nullable=False)
    lng = db.Column(db.Float, nullable=False)
    data_consulta = db.Column(db.DateTime, default=datetime.datetime.utcnow)


class Configuracao(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    chave = db.Column(db.String(50), unique=True, nullable=False)
    valor = db.Column(db.String(100), nullable=False)


class Produto(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    categoria = db.Column(db.String(50))
    unidade = db.Column(db.String(20))
    nome = db.Column(db.String(100), unique=True)
    preco = db.Column(db.Float, default=0.0)
    e_promo = db.Column(db.Boolean, default=False)
    imagem = db.Column(db.String(200), default='placeholder.jpg')
    disponivel = db.Column(db.Boolean, default=True)


class Usuario(db.Model):
    __tablename__ = 'usuarios'
    email = db.Column(db.String(120), primary_key=True)
    nome = db.Column(db.String(100), nullable=False)
    cep = db.Column(db.String(10), nullable=False)
    endereco = db.Column(db.String(200))
    numero = db.Column(db.String(10))
    complemento = db.Column(db.String(100))
    telefone = db.Column(db.String(20))
    senha_hash = db.Column(db.String(200), nullable=False)

    def set_senha(self, senha):
        self.senha_hash = generate_password_hash(senha)

    def verificar_senha(self, senha):
        return check_password_hash(self.senha_hash, senha)

    def to_dict(self):
        return {
            'email': self.email,
            'nome': self.nome,
            'cep': self.cep,
            'endereco': self.endereco,
            'numero': self.numero,
            'complemento': self.complemento,
            'telefone': self.telefone
        }


class Pedido(db.Model):
    __tablename__ = 'pedidos'
    id = db.Column(db.Integer, primary_key=True)
    data_hora = db.Column(db.DateTime, default=datetime.datetime.utcnow)
    usuario_email = db.Column(db.String(120), db.ForeignKey('usuarios.email'), nullable=True)
    nome_cliente = db.Column(db.String(100), nullable=False)
    whatsapp = db.Column(db.String(20), nullable=False)
    email = db.Column(db.String(120), nullable=False)
    cep = db.Column(db.String(9), nullable=False)
    endereco = db.Column(db.String(200), nullable=False)
    numero = db.Column(db.String(10), nullable=False)
    complemento = db.Column(db.String(100))
    pagamento = db.Column(db.String(50), nullable=False)
    horario_entrega = db.Column(db.String(50), nullable=False)
    subtotal = db.Column(db.Float, nullable=False)
    frete = db.Column(db.Float, nullable=False)
    total = db.Column(db.Float, nullable=False)
    status = db.Column(db.String(50), default='recebido')
    observacoes = db.Column(db.Text)

    itens = db.relationship('ItemPedido', backref='pedido', lazy=True, cascade='all, delete-orphan')


class ItemPedido(db.Model):
    __tablename__ = 'itens_pedido'
    id = db.Column(db.Integer, primary_key=True)
    pedido_id = db.Column(db.Integer, db.ForeignKey('pedidos.id'), nullable=False)
    produto_id = db.Column(db.Integer, db.ForeignKey('produto.id'), nullable=False)
    quantidade = db.Column(db.Float, nullable=False)
    preco_unitario = db.Column(db.Float, nullable=False)
    subtotal = db.Column(db.Float, nullable=False)

    produto = db.relationship('Produto')


# ============================================================
# FUNÇÕES AUXILIARES
# ============================================================
def calcular_distancia_km(cep_origem, cep_destino):
    """Retorna a distância em km entre dois CEPs usando cache e API AwesomeAPI."""

    def obter_coords(cep_puro):
        try:
            cep = str(cep_puro).replace('-', '').strip()
            # Verifica se já está no cache
            cache = db.session.get(CEPCache, cep)
            if cache:
                return cache.lat, cache.lng

            # Consulta API
            url = f"https://cep.awesomeapi.com.br/json/{cep}"
            response = requests.get(url, timeout=3)
            if response.status_code == 200:
                data = response.json()
                lat, lng = float(data['lat']), float(data['lng'])
                # Salva no cache
                novo_cache = CEPCache(cep=cep, lat=lat, lng=lng)
                db.session.add(novo_cache)
                db.session.commit()
                return lat, lng

            # Fallback para CEP reduzido
            cep_reduzido = cep[:5] + "000"
            url_reserva = f"https://cep.awesomeapi.com.br/json/{cep_reduzido}"
            response_reserva = requests.get(url_reserva, timeout=3)
            if response_reserva.status_code == 200:
                data = response_reserva.json()
                lat, lng = float(data['lat']), float(data['lng'])
                novo_cache = CEPCache(cep=cep, lat=lat, lng=lng)
                db.session.add(novo_cache)
                db.session.commit()
                return lat, lng
            return None
        except Exception as e:
            app.logger.error(f"Erro ao obter coordenadas do CEP {cep_puro}: {e}")
            return None

    c1 = obter_coords(cep_origem)
    c2 = obter_coords(cep_destino)
    if not c1 or not c2:
        return None

    lat1, lon1 = c1
    lat2, lon2 = c2
    R = 6371
    dLat = math.radians(lat2 - lat1)
    dLon = math.radians(lon2 - lon1)
    a = math.sin(dLat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dLon/2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
    return round(R * c, 2)


def calcular_frete(valor_carrinho, cep_destino):
    """
    Calcula o valor do frete com base no valor do carrinho e CEP de destino.
    Retorna um dicionário com valor_frete, distancia e status.
    """
    configs = {c.chave: c.valor for c in Configuracao.query.all()}
    cep_origem = configs.get('cep_origem', '12200-000')
    dist = calcular_distancia_km(cep_origem, cep_destino)

    if dist is None:
        return {"valor_frete": 15.00, "distancia": "N/A", "status": "fallback"}

    raio_max = float(configs.get('raio_maximo_km', 15))
    f_base = float(configs.get('frete_minimo', 12.00))

    if dist > raio_max:
        valor_f = f_base + ((dist - raio_max) * 5.0)
        status = "longa_distancia"
    elif valor_carrinho >= float(configs.get('frete_gratis_valor', 200)):
        valor_f = 0.0
        status = "gratis"
    else:
        valor_f = f_base
        status = "base"

    return {"valor_frete": round(valor_f, 2), "distancia": dist, "status": status}


def sincronizar_tudo_do_excel():
    """Sincroniza produtos e configurações a partir do arquivo produtos.xlsx."""
    caminho_excel = os.path.join(base_dir, 'produtos.xlsx')
    if not os.path.exists(caminho_excel):
        app.logger.warning("Arquivo produtos.xlsx não encontrado. Pulando sincronização.")
        return

    try:
        with app.app_context():
            db.create_all()
            df_prod = pd.read_excel(caminho_excel, sheet_name=0, dtype={'preco': str})
            df_conf = pd.read_excel(caminho_excel, sheet_name='Config')

            app.logger.info("Planilha de produtos carregada: %d produtos", len(df_prod))

            # Configurações
            # Atualiza configurações
            Configuracao.query.delete()
            for _, lin in df_conf.iterrows():
                chave = str(lin['Parametro']).strip()
                valor = str(lin['Valor']).strip()
                # Tenta converter para float (se for numérico) e armazena com ponto
                try:
                    # Se for um número (possivelmente com vírgula), converte para float e depois para string com ponto
                    valor_float = float(valor.replace(',', '.'))
                    valor = str(valor_float)
                except ValueError:
                    pass  # não é numérico, mantém original
                db.session.add(Configuracao(chave=chave, valor=valor))
            # Produtos
            for idx, lin in df_prod.iterrows():
                nome = str(lin.iloc[2]).strip()
                if not nome:
                    continue

                preco_str = str(lin.iloc[3]).strip().replace(',', '.')
                try:
                    preco = float(preco_str)
                except ValueError:
                    preco = 0.0
                    app.logger.warning(f"Preço inválido na linha {idx+2}: '{lin.iloc[3]}'")

                categoria = str(lin.iloc[0]).strip().lower()
                unidade = str(lin.iloc[1]).strip()
                e_promo = str(lin.iloc[4]).strip().lower() == 'x'
                imagem = str(lin.iloc[5]).strip() if pd.notna(lin.iloc[5]) else 'placeholder.jpg'

                p = Produto.query.filter_by(nome=nome).first()
                if not p:
                    p = Produto(
                        nome=nome,
                        preco=preco,
                        categoria=categoria,
                        unidade=unidade,
                        e_promo=e_promo,
                        imagem=imagem,
                        disponivel=False
                    )
                    db.session.add(p)
                    app.logger.info(f"Produto criado: {nome} (R$ {preco:.2f})")
                else:
                    p.preco = preco
                    p.categoria = categoria
                    p.unidade = unidade
                    p.e_promo = e_promo
                    p.imagem = imagem
                    app.logger.info(f"Produto atualizado: {nome} (preço R$ {preco:.2f})")

            db.session.commit()
            app.logger.info("Sincronização concluída com sucesso.")
    except Exception as e:
        app.logger.error(f"Erro na sincronização: {e}")
        db.session.rollback()


# ============================================================
# ROTAS DE CLIENTE
# ============================================================
@app.route("/")
def home():
    promos = Produto.query.filter_by(e_promo=True, disponivel=True).all()
    return render_template("home.html", promos=promos, user=session.get('user'))

@app.route('/baixar-catalogo')
def baixar_catalogo():
    # Verifica se usuário está logado
    if not session.get('user'):
        flash('Faça login ou cadastre-se para baixar o catálogo.', 'warning')
        return redirect(url_for('home'))

    # Caminho do arquivo PDF (coloque o arquivo na pasta static)
    catalogo_path = os.path.join(app.static_folder, 'CNC-Catalogo.PDF')

    # Verifica se o arquivo existe
    if not os.path.exists(catalogo_path):
        abort(404, description="Catálogo não encontrado")

    # Envia o arquivo para download
    return send_file(catalogo_path, as_attachment=True, download_name='CNC-Catalogo.pdf')


@app.route("/catalogo")
def catalogo():
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 12, type=int)
    cat = request.args.get('cat', '').strip().lower()
    busca = request.args.get('busca', '').strip()

    query = Produto.query.filter_by(disponivel=True)
    if cat:
        query = query.filter(Produto.categoria == cat)
    if busca:
        query = query.filter(Produto.nome.ilike(f"%{busca}%"))

    produtos_paginados = query.order_by(Produto.nome).paginate(
        page=page, per_page=per_page, error_out=False
    )

    return render_template(
        "catalogo.html",
        produtos=produtos_paginados.items,
        pagination=produtos_paginados,
        user=session.get('user'),
        categoria_ativa=cat,
        busca=busca
    )


@app.route('/carrinho')
def ver_carrinho():
    return render_template('carrinho.html', user=session.get('user'))


@app.route('/sucesso')
def sucesso():
    return render_template('sucesso.html')


# ============================================================
# APIs DE CONFIGURAÇÃO E FRETE
# ============================================================
@app.route('/api/config_entrega', methods=['GET'])
def api_config_entrega():
    configs = {c.chave: c.valor for c in Configuracao.query.all()}
    return jsonify({
        "frete_minimo": float(configs.get('frete_minimo', 12.00)),
        "frete_gratis_valor": float(configs.get('frete_gratis_valor', 200.00)),
        "valor_pedido_minimo": float(configs.get('valor_pedido_minimo', 30.00)),
        "raio_maximo_km": float(configs.get('raio_maximo_km', 15))
    })


@app.route('/api/calcular-frete', methods=['POST'])
@limiter.limit("10 per minute")
def api_calcular_frete():
    dados = request.json
    if not dados:
        return jsonify({"erro": "Dados não fornecidos"}), 400

    valor_c = float(dados.get('valor_carrinho', 0))
    cep_dest = str(dados.get('cep', '')).replace('-', '').strip()

    if not cep_dest or len(cep_dest) != 8:
        return jsonify({"erro": "CEP inválido"}), 400

    resultado = calcular_frete(valor_c, cep_dest)
    return jsonify(resultado)


# ============================================================
# FINALIZAÇÃO DE PEDIDO (ENTREGA)
# ============================================================
@app.route('/api/processar-pedido', methods=['POST'])
@limiter.limit("5 per minute")
def processar_pedido():
    try:
        dados = request.json
        if not dados:
            return jsonify({"erro": "Dados não fornecidos"}), 400

        itens_c = dados.get('itens', [])
        if not itens_c:
            return jsonify({"erro": "Carrinho vazio"}), 400

        configs = {c.chave: c.valor for c in Configuracao.query.all()}
        cep_destino = str(dados.get('cep', '')).replace('-', '').strip()

        if not cep_destino or len(cep_destino) != 8:
            return jsonify({"erro": "CEP inválido"}), 400

        subtotal = 0.0
        itens_para_salvar = []
        for item in itens_c:
            p = db.session.get(Produto, item['id'])
            if p and p.disponivel:
                valor_item = p.preco * float(item['quantidade'])
                subtotal += valor_item
                itens_para_salvar.append({
                    'produto': p,
                    'quantidade': float(item['quantidade']),
                    'preco_unitario': p.preco,
                    'subtotal': valor_item
                })

        if not itens_para_salvar:
            return jsonify({"erro": "Nenhum item válido no carrinho"}), 400

        # Recalcula frete usando a função auxiliar
        frete_data = calcular_frete(subtotal, cep_destino)
        frete = frete_data['valor_frete']
        total = subtotal + frete
        num_ped = datetime.datetime.now().strftime("%Y%m%d") + "-" + str(random.randint(1000, 9999))

        usuario_email = session.get('user', {}).get('email') if session.get('user') else None

        novo_pedido = Pedido(
            usuario_email=usuario_email,
            nome_cliente=dados['cliente_nome'],
            whatsapp=dados['whatsapp'],
            email=dados['email'],
            cep=dados['cep'],
            endereco=dados['endereco'],
            numero=dados['numero'],
            complemento=dados.get('complemento', ''),
            pagamento=dados['pagamento'],
            horario_entrega=dados['horario_entrega'],
            subtotal=subtotal,
            frete=frete,
            total=total,
            status='recebido'
        )
        db.session.add(novo_pedido)
        db.session.flush()

        for item in itens_para_salvar:
            item_pedido = ItemPedido(
                pedido_id=novo_pedido.id,
                produto_id=item['produto'].id,
                quantidade=item['quantidade'],
                preco_unitario=item['preco_unitario'],
                subtotal=item['subtotal']
            )
            db.session.add(item_pedido)

        db.session.commit()

        # Envia e-mail
        lista_corpo_email = [f"• {item['quantidade']}{item['produto'].unidade} {item['produto'].nome}: R$ {item['subtotal']:.2f}" for item in itens_para_salvar]

        destinatario = configs.get('email_entregador', 'wendreis@yahoo.com.br')
        msg = Message(f"PEDIDO #{num_ped} - CNC Imports", recipients=[destinatario])
        msg.body = (
            f"Cliente: {dados['cliente_nome']}\n"
            f"WhatsApp: {dados['whatsapp']}\n"
            f"E-mail: {dados['email']}\n"
            f"Endereço: {dados['endereco']}, {dados['numero']} - {dados['cep']}\n"
            f"Complemento: {dados.get('complemento', '')}\n"
            f"Pagamento: {dados['pagamento']}\n"
            f"Entrega: {dados['horario_entrega']}\n\n"
            f"ITENS:\n" + "\n".join(lista_corpo_email) +
            f"\n\nSubtotal: R$ {subtotal:.2f}\n"
            f"Frete: R$ {frete:.2f}\n"
            f"TOTAL: R$ {total:.2f}"
        )
        mail.send(msg)

        return jsonify({
            "status": "sucesso",
            "pedido": num_ped,
            "total_final": total,
            "id_pedido": novo_pedido.id
        })

    except Exception as e:
        db.session.rollback()
        app.logger.error(f"Erro em processar_pedido: {str(e)}")
        return jsonify({"erro": "Erro interno ao processar pedido"}), 500


# ============================================================
# MERCADO PAGO - PAGAMENTOS (PIX)
# ============================================================
@app.route('/api/criar-pagamento-pix', methods=['POST'])
@limiter.limit("5 per minute")
def criar_pagamento_pix():
    """
    Cria um pagamento PIX no Mercado Pago com 5% de desconto
    """
    try:
        dados = request.json
        itens = dados.get('itens', [])
        comprador = dados.get('comprador', {})
        frete = dados.get('frete', 0)

        if not itens or not comprador:
            return jsonify({"erro": "Dados incompletos"}), 400

        # Converte itens para o formato esperado (com nome, preco, quantidade, unidade)
        itens_formatados = []
        for item in itens:
            p = db.session.get(Produto, item['id'])
            if p and p.disponivel:
                itens_formatados.append({
                    'id': p.id,
                    'nome': p.nome,
                    'preco': p.preco,
                    'quantidade': float(item['quantidade']),
                    'unidade': p.unidade
                })

        if not itens_formatados:
            return jsonify({"erro": "Nenhum item válido"}), 400

        # Calcula subtotal
        subtotal = sum(i['preco'] * i['quantidade'] for i in itens_formatados)
        total_com_desconto = subtotal * 0.95  # 5% de desconto PIX
        total_final = total_com_desconto + frete

        # Determina usuário logado (se houver)
        usuario_email = session.get('user', {}).get('email') if session.get('user') else None

        # Cria o pedido no banco com status 'aguardando_pagamento'
        novo_pedido = Pedido(
            usuario_email=usuario_email,
            nome_cliente=comprador['nome'],
            whatsapp=comprador.get('whatsapp', ''),
            email=comprador['email'],
            cep=comprador.get('cep', ''),
            endereco=comprador.get('endereco', ''),
            numero=comprador.get('numero', ''),
            complemento=comprador.get('complemento', ''),
            pagamento='PIX Online',
            horario_entrega=comprador.get('horario_entrega', ''),
            subtotal=subtotal,
            frete=frete,
            total=total_final,
            status='aguardando_pagamento'
        )
        db.session.add(novo_pedido)
        db.session.flush()

        # Adiciona itens do pedido
        for item in itens_formatados:
            item_pedido = ItemPedido(
                pedido_id=novo_pedido.id,
                produto_id=item['id'],
                quantidade=item['quantidade'],
                preco_unitario=item['preco'],
                subtotal=item['preco'] * item['quantidade']
            )
            db.session.add(item_pedido)

        db.session.commit()

        # Cria preferência no Mercado Pago
        resultado = mp_integration.criar_preferencia_pix(
            itens=itens_formatados,
            pedido_id=novo_pedido.id,
            comprador=comprador,
            total_com_desconto=total_com_desconto
        )

        if resultado["sucesso"]:
            return jsonify({
                "sucesso": True,
                "pedido_id": novo_pedido.id,
                "preference_id": resultado["id"],
                "init_point": resultado["init_point"],
                "total_original": subtotal,
                "total_com_desconto": resultado["total_com_desconto"]
            })
        else:
            # Se erro no MP, remove o pedido
            db.session.delete(novo_pedido)
            db.session.commit()
            return jsonify({"erro": resultado.get("erro", "Erro no Mercado Pago")}), 500

    except Exception as e:
        db.session.rollback()
        app.logger.error(f"Erro em criar_pagamento_pix: {str(e)}")
        return jsonify({"erro": str(e)}), 500


@app.route('/webhook/mercadopago', methods=['POST'])
def mercadopago_webhook():
    """
    Webhook para receber notificações do Mercado Pago (IPN)
    """
    data = request.json
    app.logger.info(f"Notificação recebida: {json.dumps(data, indent=2)}")

    resultado = mp_integration.processar_notificacao(data)

    if resultado:
        # Atualizar pedido no banco
        pedido = db.session.get(Pedido, int(resultado["external_reference"]))
        if pedido:
            if resultado["status"] == "approved":
                pedido.status = "pago"
                # Opcional: enviar e-mail de confirmação
            elif resultado["status"] == "rejected":
                pedido.status = "falhou"
            elif resultado["status"] == "pending":
                pedido.status = "processando"
            db.session.commit()

    return "OK", 200

@app.route('/api/criar-pedido-pix-manual', methods=['POST'])
@limiter.limit("5 per minute")
def criar_pedido_pix_manual():
    """
    Cria um pedido com pagamento PIX manual (chave) e aplica 5% de desconto.
    Retorna a chave PIX configurada no banco.
    """
    try:
        dados = request.json
        itens = dados.get('itens', [])
        comprador = dados.get('comprador', {})
        frete = dados.get('frete', 0)

        if not itens or not comprador:
            return jsonify({"erro": "Dados incompletos"}), 400

        # Busca a chave PIX na tabela Configuracao
        chave_pix_config = Configuracao.query.filter_by(chave='chave_pix').first()
        chave_pix = chave_pix_config.valor if chave_pix_config else 'chave_pix_nao_configurada'

        # Valida e calcula os itens
        itens_formatados = []
        subtotal = 0.0
        for item in itens:
            p = db.session.get(Produto, item['id'])
            if p and p.disponivel:
                quantidade = float(item['quantidade'])
                valor_item = p.preco * quantidade
                subtotal += valor_item
                itens_formatados.append({
                    'produto': p,
                    'quantidade': quantidade,
                    'preco_unitario': p.preco,
                    'subtotal': valor_item
                })

        if not itens_formatados:
            return jsonify({"erro": "Nenhum item válido"}), 400

        # Aplica desconto de 5%
        total_com_desconto = subtotal * 0.95
        total_final = total_com_desconto + frete

        usuario_email = session.get('user', {}).get('email') if session.get('user') else None

        # Cria o pedido com status 'aguardando_pagamento'
        novo_pedido = Pedido(
            usuario_email=usuario_email,
            nome_cliente=comprador['nome'],
            whatsapp=comprador.get('whatsapp', ''),
            email=comprador['email'],
            cep=comprador.get('cep', ''),
            endereco=comprador.get('endereco', ''),
            numero=comprador.get('numero', ''),
            complemento=comprador.get('complemento', ''),
            pagamento='PIX Manual',
            horario_entrega=comprador.get('horario_entrega', ''),
            subtotal=subtotal,
            frete=frete,
            total=total_final,
            status='aguardando_pagamento'
        )
        db.session.add(novo_pedido)
        db.session.flush()

        for item in itens_formatados:
            item_pedido = ItemPedido(
                pedido_id=novo_pedido.id,
                produto_id=item['produto'].id,
                quantidade=item['quantidade'],
                preco_unitario=item['preco_unitario'],
                subtotal=item['subtotal']
            )
            db.session.add(item_pedido)

        db.session.commit()

        # Retorna sucesso com a chave PIX
        return jsonify({
            "sucesso": True,
            "pedido_id": novo_pedido.id,
            "chave_pix": chave_pix,
            "total_original": subtotal,
            "total_com_desconto": total_com_desconto,
            "total_final": total_final
        })

    except Exception as e:
        db.session.rollback()
        print(f"Erro em criar_pedido_pix_manual: {str(e)}")
        return jsonify({"erro": str(e)}), 500

@app.route('/pagamento/sucesso')
def pagamento_sucesso():
    """Página de sucesso após pagamento"""
    payment_id = request.args.get('payment_id')
    status = request.args.get('status')
    external_ref = request.args.get('external_reference')

    # Consulta o pedido para exibir informações
    pedido = None
    if external_ref:
        pedido = db.session.get(Pedido, int(external_ref))

    return render_template('sucesso_pagamento.html',
                           payment_id=payment_id,
                           status=status,
                           pedido=pedido)


@app.route('/pagamento/falha')
def pagamento_falha():
    """Página de falha no pagamento"""
    return render_template('falha_pagamento.html')


@app.route('/api/verificar-status-pagamento/<int:pedido_id>', methods=['GET'])
def verificar_status_pagamento(pedido_id):
    """Verifica o status atual de um pedido"""
    pedido = db.session.get(Pedido, pedido_id)
    if pedido:
        return jsonify({
            "pedido_id": pedido.id,
            "status": pedido.status,
            "total": pedido.total
        })
    return jsonify({"erro": "Pedido não encontrado"}), 404


# ============================================================
# LOGIN E CADASTRO
# ============================================================
@app.route('/api/login', methods=['POST'])
@limiter.limit("5 per minute")
def login():
    d = request.json
    if not d or not d.get('email') or not d.get('senha'):
        return jsonify({"mensagem": "E-mail e senha obrigatórios"}), 400

    u = db.session.get(Usuario, d['email'])
    if u and u.verificar_senha(d['senha']):
        session['user'] = u.to_dict()
        return jsonify({"mensagem": "Login ok!", "nome": u.nome}), 200
    return jsonify({"mensagem": "E-mail ou senha inválidos"}), 401


@app.route('/api/cadastro', methods=['POST'])
@limiter.limit("3 per minute")
def cadastro_usuario():
    d = request.json
    if not d or not d.get('email') or not d.get('nome') or not d.get('cep') or not d.get('senha'):
        return jsonify({"mensagem": "Campos obrigatórios: email, nome, cep, senha"}), 400

    if db.session.get(Usuario, d['email']):
        return jsonify({"mensagem": "E-mail já cadastrado"}), 400

    try:
        novo = Usuario(
            email=d['email'],
            nome=d['nome'],
            cep=d['cep'],
            endereco=d.get('endereco', ''),
            numero=d.get('numero', ''),
            complemento=d.get('complemento', ''),
            telefone=d.get('telefone', '')
        )
        novo.set_senha(d['senha'])
        db.session.add(novo)
        db.session.commit()
        session['user'] = novo.to_dict()
        return jsonify({"mensagem": "Cadastro realizado com sucesso!"}), 201
    except Exception as e:
        db.session.rollback()
        app.logger.error(f"Erro no cadastro: {str(e)}")
        return jsonify({"mensagem": f"Erro no cadastro: {str(e)}"}), 400


@app.route('/logout')
def logout():
    session.pop('user', None)
    return redirect(url_for('home'))

# rotas do rodapé

@app.route('/sobre')
def sobre():
    return render_template('sobre.html', user=session.get('user'))

@app.route('/como-comprar')
def como_comprar():
    return render_template('como_comprar.html', user=session.get('user'))

@app.route('/politica-seguranca')
def politica_seguranca():
    return render_template('politica_seguranca.html', user=session.get('user'))

@app.route('/fretes-envio')
def fretes_envio():
    return render_template('fretes_envio.html', user=session.get('user'))

@app.route('/facilidades-pagamento')
def facilidades_pagamento():
    return render_template('facilidades_pagamento.html', user=session.get('user'))

@app.route('/garantia')
def garantia():
    return render_template('garantia.html', user=session.get('user'))

@app.route('/trocas-devolucoes')
def trocas_devolucoes():
    return render_template('trocas_devolucoes.html', user=session.get('user'))


@app.route('/fale-conosco', methods=['GET'])
def fale_conosco():
    return render_template('fale_conosco.html', user=session.get('user'))


@app.route('/api/enviar-contato', methods=['POST'])
@limiter.limit("3 per minute")
def enviar_contato():
    try:
        nome = request.form.get('nome')
        email = request.form.get('email')
        telefone = request.form.get('telefone', '')
        mensagem = request.form.get('mensagem')

        if not nome or not email or not mensagem:
            return jsonify({"erro": "Campos obrigatórios não preenchidos"}), 400

        configs = {c.chave: c.valor for c in Configuracao.query.all()}
        destinatario = configs.get('email_entregador', 'wendreis@yahoo.com.br')

        assunto = f"Contato via site - {nome}"
        corpo = f"""
        Nome: {nome}
        E-mail: {email}
        Telefone: {telefone}

        Mensagem:
        {mensagem}
        """

        msg = Message(assunto, recipients=[destinatario])
        msg.body = corpo
        msg.reply_to = email

        mail.send(msg)

        return jsonify({"sucesso": True}), 200

    except Exception as e:
        print(f"Erro ao enviar e-mail de contato: {str(e)}")
        return jsonify({"erro": "Erro interno"}), 500

# ============================================================
# ADMIN
# ============================================================
@app.route('/admin')
def admin_painel():
    if not session.get('user') or session['user']['email'] != 'wendreis@yahoo.com.br':
        return redirect(url_for('home'))
    return render_template('admin.html')


@app.route('/api/admin/atualizar-precos', methods=['POST'])
@limiter.limit("2 per minute")
def api_atualizar_precos():
    file = request.files.get('planilha')
    margem = (float(request.form.get('margem', 0)) / 100) + 1
    try:
        # Força a coluna de preço (índice 1) a ser lida como string
        df = pd.read_excel(file, dtype={1: str})
        Produto.query.update({Produto.disponivel: False})
        for _, lin in df.iterrows():
            nome_prod = str(lin.iloc[0]).strip()
            preco_custo = float(str(lin.iloc[1]).replace(',', '.'))
            p = Produto.query.filter_by(nome=nome_prod).first()
            if p and preco_custo > 0:
                p.preco = round(preco_custo * margem, 2)
                p.disponivel = True
        db.session.commit()
        return jsonify({"sucesso": "Preços atualizados com sucesso!"})
    except Exception as e:
        db.session.rollback()
        return jsonify({"erro": str(e)}), 500


# ============================================================
# HTTPS FORCE (OPCIONAL - COMENTADO)
# ============================================================
# @app.before_request
# def force_https():
#     if not request.is_secure and not app.debug:
#         return redirect(request.url.replace('http://', 'https://'), code=301)


# ============================================================
# INICIALIZAÇÃO
# ============================================================
if __name__ == "__main__":
    with app.app_context():
        db.create_all()
        # Cria índices para melhor performance
        db.session.execute(text('CREATE INDEX IF NOT EXISTS idx_produto_disponivel ON produto (disponivel)'))
        db.session.execute(text('CREATE INDEX IF NOT EXISTS idx_produto_categoria ON produto (categoria)'))
        db.session.execute(text('CREATE INDEX IF NOT EXISTS idx_produto_nome ON produto (nome)'))
        db.session.commit()
    sincronizar_tudo_do_excel()
    app.run(debug=False)