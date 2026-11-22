import json
import os
import re
import sqlite3
import traceback
from datetime import datetime
import edge_tts
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
from google import genai
from google.genai import types

app = FastAPI(title="10 Minutos com Deus - API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

API_KEY = os.environ.get("GEMINI_API_KEY", "AQ.Ab8RN6Ko6Hq7oj7oL-Zl1hX2rC6Z97sW4UA6R7l4UsTQIJzgYw")
client = genai.Client(api_key=API_KEY)

PASTA_AUDIOS = "audios_gerados"
DB_PATH = "estudos.db"
os.makedirs(PASTA_AUDIOS, exist_ok=True)

GITHUB_USER = "fabiorv50-cmd"
GITHUB_REPO = "dez_minutos_com_Deus"
GITHUB_BRANCH = "main"


def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS estudos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            slug TEXT NOT NULL,
            passagem TEXT NOT NULL,
            voz TEXT NOT NULL,
            roteiro TEXT NOT NULL,
            arquivo_mp3 TEXT NOT NULL,
            criado_em TEXT NOT NULL,
            UNIQUE(slug, voz)
        )
        """
    )
    conn.commit()
    conn.close()


init_db()


class PedidoEstudo(BaseModel):
    passagem: str
    voz: str = "masculina"


def limpar_nome_arquivo(texto: str) -> str:
    texto = texto.lower().strip()
    texto = re.sub(r"[^a-z0-9]+", "_", texto)
    return texto.strip("_")


def gerar_roteiro_explicado(passagem: str) -> str:
    modelos_disponiveis = [
        "gemini-flash-lite-latest",
        "gemini-flash-latest",
        "gemini-3.8-flash",
    ]

    def chamar_gemini(prompt_texto: str) -> str:
        ultimo_erro = None
        for nome_modelo in modelos_disponiveis:
            try:
                print(f"      -> Tentando modelo: {nome_modelo}...", flush=True)
                response = client.models.generate_content(
                    model=nome_modelo,
                    contents=prompt_texto,
                    config=types.GenerateContentConfig(
                        temperature=0.7,
                        http_options=types.HttpOptions(timeout=35_000),
                        safety_settings=[
                            types.SafetySetting(category="HARM_CATEGORY_HARASSMENT", threshold="BLOCK_NONE"),
                            types.SafetySetting(category="HARM_CATEGORY_HATE_SPEECH", threshold="BLOCK_NONE"),
                            types.SafetySetting(category="HARM_CATEGORY_SEXUALLY_EXPLICIT", threshold="BLOCK_NONE"),
                            types.SafetySetting(category="HARM_CATEGORY_DANGEROUS_CONTENT", threshold="BLOCK_NONE"),
                        ],
                    ),
                )
                texto = (response.text or "") if response else ""
                if not texto and response and response.candidates:
                    partes = response.candidates[0].content.parts or []
                    texto = "".join(p.text for p in partes if getattr(p, "text", None))
                if texto.strip():
                    print(f"      -> Sucesso com {nome_modelo} ({len(texto.strip())} chars)", flush=True)
                    return texto.strip()
            except Exception as err:
                ultimo_erro = err
                erro_txt = str(err)
                print(f"      -> Falha em {nome_modelo}: {erro_txt[:110]}", flush=True)
                if any(
                    cod in erro_txt
                    for cod in ("503", "UNAVAILABLE", "404", "NOT_FOUND", "400", "INVALID_ARGUMENT", "DEADLINE_EXCEEDED", "timeout", "Timeout")
                ):
                    continue
                raise err

        if ultimo_erro:
            raise ultimo_erro
        raise ValueError("Nenhum modelo retornou texto.")

    prompt_parte1 = f"""
    Você é o narrador do aplicativo "10 Minutos com Deus". Seu tom é acolhedor, calmo, claro e profundo.
    Escreva a PRIMEIRA METADE de um áudio devocional sobre: **{passagem}**.

    REGRAS OBRIGATÓRIAS:
    - Escreva entre 600 e 700 palavras.
    - Escreva EXCLUSIVAMENTE o texto falado. NÃO use asteriscos, hashtags, colchetes [pausa], nem abreviações como "v. 1" (escreva "versículo um").
    - Separe os parágrafos com uma linha em branco.

    CONTEÚDO DESTA PARTE:
    1. Saudação breve ("Bem-vindo ao seu momento de 10 Minutos com Deus...") e o contexto histórico de {passagem}.
    2. Leitura e explicação detalhada da PRIMEIRA METADE dos versículos de {passagem}, em blocos de 1 a 3 versículos. Não faça encerramento nem oração ainda.
    """

    prompt_parte2 = f"""
    Você é o narrador do aplicativo "10 Minutos com Deus". Continue diretamente a explicação do áudio devocional sobre: **{passagem}** (sem repetir saudação inicial).

    REGRAS OBRIGATÓRIAS:
    - Escreva entre 600 e 700 palavras.
    - Escreva EXCLUSIVAMENTE o texto falado. NÃO use asteriscos, hashtags, colchetes [pausa], nem abreviações como "v. 1" (escreva "versículo um").
    - Separe os parágrafos com uma linha em branco.

    CONTEÚDO DESTA PARTE:
    1. Leitura e explicação detalhada da SEGUNDA METADE dos versículos de {passagem}, em blocos de 1 a 3 versículos.
    2. Aplicação prática para os dias de hoje e uma oração curta de encerramento.
    """

    parte1 = chamar_gemini(prompt_parte1)
    parte2 = chamar_gemini(prompt_parte2)
    return f"{parte1}\n\n{parte2}".strip()


async def gerar_audio_com_legenda(roteiro: str, voz_tts: str, caminho_mp3: str, caminho_legenda_json: str):
    """Gera o arquivo MP3 e o JSON de legendas sincronizadas por frase diretamente pelo Edge-TTS."""
    communicate = edge_tts.Communicate(roteiro, voz_tts, rate="-5%")
    legendas = []

    with open(caminho_mp3, "wb") as arquivo_audio:
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                arquivo_audio.write(chunk["data"])
            elif chunk["type"] == "SentenceBoundary":
                inicio_ms = int(chunk["offset"] / 10_000)
                duracao_ms = int(chunk["duration"] / 10_000)
                legendas.append({
                    "inicio_ms": inicio_ms,
                    "fim_ms": inicio_ms + duracao_ms,
                    "texto": chunk["text"].strip(),
                })

    with open(caminho_legenda_json, "w", encoding="utf-8") as f:
        json.dump(legendas, f, ensure_ascii=False, indent=2)

    return legendas


@app.get("/estudos")
async def listar_estudos_salvos():
    """Retorna todos os estudos já convertidos e salvos no banco de dados."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, passagem, voz, roteiro, arquivo_mp3, criado_em FROM estudos ORDER BY id DESC"
    )
    rows = cursor.fetchall()
    conn.close()

    resultado = []
    for row in rows:
        caminho_mp3 = os.path.join(PASTA_AUDIOS, row["arquivo_mp3"])
        if os.path.exists(caminho_mp3):
            nome_legenda = row["arquivo_mp3"].replace(".mp3", "_legenda.json")
            resultado.append(
                {
                    "id": row["id"],
                    "passagem": row["passagem"],
                    "voz": row["voz"],
                    "roteiro": row["roteiro"],
                    "cache": True,
                    "audio_url": f"/audio/{row['arquivo_mp3']}",
                    "legenda_url": f"/audio/{nome_legenda}",
                    "criado_em": row["criado_em"],
                }
            )
    return resultado


@app.post("/gerar-audio")
async def obter_ou_gerar_estudo(pedido: PedidoEstudo):
    if not pedido.passagem.strip():
        raise HTTPException(status_code=400, detail="Informe uma passagem bíblica.")

    voz_norm = pedido.voz.lower().strip()
    voz_tts = (
        "pt-BR-FranciscaNeural"
        if voz_norm == "feminina"
        else "pt-BR-AntonioNeural"
    )

    slug = limpar_nome_arquivo(pedido.passagem)
    nome_mp3 = f"{slug}_{voz_norm}.mp3"
    nome_legenda = f"{slug}_{voz_norm}_legenda.json"
    caminho_mp3 = os.path.join(PASTA_AUDIOS, nome_mp3)
    caminho_legenda = os.path.join(PASTA_AUDIOS, nome_legenda)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM estudos WHERE slug = ? AND voz = ?",
        (slug, voz_norm),
    )
    existente = cursor.fetchone()

    if existente and os.path.exists(caminho_mp3) and os.path.exists(caminho_legenda):
        conn.close()
        return {
            "app": "10 Minutos com Deus",
            "passagem": existente["passagem"],
            "voz": existente["voz"],
            "cache": True,
            "roteiro": existente["roteiro"],
            "audio_url": f"/audio/{existente['arquivo_mp3']}",
            "legenda_url": f"/audio/{nome_legenda}",
        }

    try:
        cursor.execute("SELECT roteiro FROM estudos WHERE slug = ? LIMIT 1", (slug,))
        outro_registro = cursor.fetchone()

        if outro_registro:
            roteiro = outro_registro["roteiro"]
        else:
            print(f"1. Gerando roteiro inédito com Gemini para: {pedido.passagem}...")
            roteiro = gerar_roteiro_explicado(pedido.passagem)

        print(f"2. Convertendo em voz e legenda sincronizada ({voz_norm})...")
        await gerar_audio_com_legenda(roteiro, voz_tts, caminho_mp3, caminho_legenda)

        agora = datetime.now().strftime("%d/%m/%Y %H:%M")
        cursor.execute(
            """
            INSERT OR REPLACE INTO estudos (slug, passagem, voz, roteiro, arquivo_mp3, criado_em)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (slug, pedido.passagem.strip(), voz_norm, roteiro, nome_mp3, agora),
        )
        conn.commit()
        conn.close()
        print(f"3. Salvo com sucesso: {pedido.passagem}")

        return {
            "app": "10 Minutos com Deus",
            "passagem": pedido.passagem.strip(),
            "voz": voz_norm,
            "cache": False,
            "roteiro": roteiro,
            "audio_url": f"/audio/{nome_mp3}",
            "legenda_url": f"/audio/{nome_legenda}",
        }
    except Exception as e:
        conn.close()
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Erro ao gerar estudo: {str(e)}")


@app.get("/audio/{nome_arquivo}")
async def baixar_ou_tocar_audio(nome_arquivo: str):
    caminho = os.path.join(PASTA_AUDIOS, nome_arquivo)
    if not os.path.exists(caminho):
        raise HTTPException(status_code=404, detail="Arquivo não encontrado.")
    media = "application/json" if nome_arquivo.endswith(".json") else "audio/mpeg"
    return FileResponse(caminho, media_type=media, filename=nome_arquivo)


@app.get("/exportar-catalogo")
async def exportar_catalogo_github():
    """Gera o arquivo catalogo.json com áudios e legendas sincronizadas prontos para o GitHub."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, passagem, voz, roteiro, arquivo_mp3, criado_em FROM estudos ORDER BY id ASC"
    )
    rows = cursor.fetchall()
    conn.close()

    base_raw = f"https://raw.githubusercontent.com/{GITHUB_USER}/{GITHUB_REPO}/{GITHUB_BRANCH}/audios_gerados"
    catalogo = []

    for row in rows:
        caminho_mp3 = os.path.join(PASTA_AUDIOS, row["arquivo_mp3"])
        nome_legenda = row["arquivo_mp3"].replace(".mp3", "_legenda.json")
        if os.path.exists(caminho_mp3):
            catalogo.append(
                {
                    "id": row["id"],
                    "passagem": row["passagem"],
                    "voz": row["voz"],
                    "roteiro": row["roteiro"],
                    "audio_url": f"{base_raw}/{row['arquivo_mp3']}",
                    "legenda_url": f"{base_raw}/{nome_legenda}",
                    "criado_em": row["criado_em"],
                }
            )

    with open("catalogo.json", "w", encoding="utf-8") as f:
        json.dump(catalogo, f, ensure_ascii=False, indent=2)

    return {
        "mensagem": "catalogo.json atualizado com sucesso!",
        "total_estudos": len(catalogo),
        "instrucao": "Agora faça o Commit e Push do catalogo.json e da pasta audios_gerados para o GitHub.",
    }