import mercadopago
import os
from dotenv import load_dotenv

load_dotenv()


class MercadoPagoIntegration:
    def __init__(self):
        self.access_token = os.environ.get('MERCADO_PAGO_ACCESS_TOKEN')
        self.public_key = os.environ.get('MERCADO_PAGO_PUBLIC_KEY')

        if not self.access_token:
            raise ValueError("MERCADO_PAGO_ACCESS_TOKEN não configurado")

        self.sdk = mercadopago.SDK(self.access_token)

    def criar_preferencia_pix(self, itens, pedido_id, comprador, total_com_desconto=None):
        items_mp = []
        for item in itens:
            items_mp.append({
                "id": str(item['id']),
                "title": item['nome'],
                "description": f"{item['nome']} - {item['quantidade']}{item.get('unidade', 'un')}",
                "quantity": int(item['quantidade']),
                "unit_price": float(item['preco']),
                "currency_id": "BRL"
            })

        preference_data = {
            "items": items_mp,
            "payer": {
                "name": comprador['nome'].split()[0] if comprador['nome'] else "Cliente",
                "surname": " ".join(comprador['nome'].split()[1:]) if len(comprador['nome'].split()) > 1 else "",
                "email": comprador['email'],
                "phone": {
                    "area_code": comprador.get('whatsapp', '')[:2] if comprador.get('whatsapp') else "",
                    "number": comprador.get('whatsapp', '')[2:] if comprador.get('whatsapp') else ""
                }
            },
            "back_urls": {
                "success": "https://seu-site.com/pagamento/sucesso",  # Substitua pelo seu domínio
                "failure": "https://seu-site.com/pagamento/falha",
                "pending": "https://seu-site.com/pagamento/pendente"
            },
            "auto_return": "approved",
            "external_reference": str(pedido_id),
            "payment_methods": {
                "excluded_payment_methods": [],
                "excluded_payment_types": [
                    {"id": "credit_card"},
                    {"id": "debit_card"},
                    {"id": "ticket"}
                ],
                "installments": 1
            },
            "statement_descriptor": "CNC IMPORTS",
            "notification_url": "https://seu-site.com/webhook/mercadopago"  # URL pública para webhook
        }

        try:
            preference_response = self.sdk.preference().create(preference_data)
            if preference_response["status"] == 201:
                return {
                    "sucesso": True,
                    "id": preference_response["response"]["id"],
                    "init_point": preference_response["response"]["init_point"],
                    "sandbox_init_point": preference_response["response"].get("sandbox_init_point"),
                    "total_com_desconto": total_com_desconto
                }
            else:
                return {"sucesso": False, "erro": "Erro ao criar preferência"}
        except Exception as e:
            print(f"Erro Mercado Pago: {str(e)}")
            return {"sucesso": False, "erro": str(e)}

    def get_public_key(self):
        return self.public_key

    def processar_notificacao(self, data):
        try:
            payment_id = data.get("data", {}).get("id")
            if not payment_id:
                return False
            payment_info = self.sdk.payment().get(payment_id)
            if payment_info["status"] == 200:
                payment = payment_info["response"]
                status = payment["status"]
                external_ref = payment.get("external_reference")
                return {
                    "payment_id": payment_id,
                    "status": status,
                    "external_reference": external_ref
                }
            return False
        except Exception as e:
            print(f"Erro na notificação: {str(e)}")
            return False