import os
import pandas as pd  # Importa o Pandas para ler o Excel
from flask import Flask, render_template


def create_app():
    app = Flask(__name__, root_path=os.getcwd())

    @app.get("/")
    def home():
        return render_template("home.html")

    @app.get("/catalogo")
    def catalogo():
        try:
            # Lê a planilha Excel
            df = pd.read_excel('produtos.xlsx')

            # Converte a tabela do Excel para uma lista de dicionários (o formato que o HTML entende)
            lista_produtos = df.to_dict(orient='records')
        except Exception as e:
            print(f"Erro ao ler o Excel: {e}")
            lista_produtos = []  # Retorna lista vazia se der erro no arquivo

        return render_template("catalogo.html", produtos=lista_produtos)

    return app


# Crie o objeto app aqui fora para o Render conseguir enxergá-lo
app = create_app()

if __name__ == "__main__":
    # O app.run só será usado quando você rodar no seu computador (local)
    app.run(debug=True)