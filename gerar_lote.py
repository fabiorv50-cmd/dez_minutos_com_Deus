import sys
import asyncio
import sqlite3
import os
import time
from datetime import datetime
from main import (
    DB_PATH,
    PASTA_AUDIOS,
    limpar_nome_arquivo,
    gerar_roteiro_explicado,
    gerar_audio_com_legenda,
)

# Força o terminal do PyCharm a exibir cada mensagem na hora, sem buffer
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(line_buffering=True)

LIVROS_NT = [
    ("Mateus", 28),
    ("Marcos", 16),
    ("Lucas", 24),
    ("João", 21),
    ("Atos", 28),
    ("Romanos", 16),
    ("1 Coríntios", 16),
    ("2 Coríntios", 13),
    ("Gálatas", 6),
    ("Efésios", 6),
    ("Filipenses", 4),
    ("Colossenses", 4),
    ("1 Tessalonicenses", 5),
    ("2 Tessalonicenses", 3),
    ("1 Timóteo", 6),
    ("2 Timóteo", 4),
    ("Tito", 3),
    ("Filemom", 1),
    ("Hebreus", 13),
    ("Tiago", 5),
    ("1 Pedro", 5),
    ("2 Pedro", 3),
    ("1 João", 5),
    ("2 João", 1),
    ("3 João", 1),
    ("Judas", 1),
    ("Apocalipse", 22),
]

VOZ_ESCOLHIDA = "masculina"
PAUSA_ENTRE_ESTUDOS = 25


def log(msg: str):
    hora = datetime.now().strftime("%H:%M:%S")
    print(f"[{hora}] {msg}", flush=True)


async def mostrar_progresso_vivo(etapa: str, tarefa):
    """Imprime um aviso a cada 5 segundos enquanto a tarefa roda para você ver que não travou."""
    inicio = time.time()
    while not tarefa.done():
        await asyncio.sleep(5)
        if not tarefa.done():
            decorrido = int(time.time() - inicio)
            log(f"   ... {etapa} em andamento ({decorrido}s)...")
    return await tarefa


def montar_lista_nt():
    lista = []
    for livro, total_caps in LIVROS_NT:
        for cap in range(1, total_caps + 1):
            lista.append(f"{livro} {cap}")
    return lista


async def processar_novo_testamento():
    voz_tts = (
        "pt-BR-FranciscaNeural"
        if VOZ_ESCOLHIDA == "feminina"
        else "pt-BR-AntonioNeural"
    )

    passagens = montar_lista_nt()
    total = len(passagens)

    log(f"Iniciando processamento de {total} capítulos do Novo Testamento (Voz: {VOZ_ESCOLHIDA})...")

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    for indice, passagem in enumerate(passagens, start=1):
        slug = limpar_nome_arquivo(passagem)
        nome_mp3 = f"{slug}_{VOZ_ESCOLHIDA}.mp3"
        nome_legenda = f"{slug}_{VOZ_ESCOLHIDA}_legenda.json"
        caminho_mp3 = os.path.join(PASTA_AUDIOS, nome_mp3)
        caminho_legenda = os.path.join(PASTA_AUDIOS, nome_legenda)

        cursor.execute(
            "SELECT roteiro FROM estudos WHERE slug = ? AND voz = ?",
            (slug, VOZ_ESCOLHIDA),
        )
        registro = cursor.fetchone()

        if registro and os.path.exists(caminho_mp3) and os.path.exists(caminho_legenda):
            log(f"[{indice}/{total}] Já salvo no banco (MP3 + legenda): {passagem} (pulando)")
            continue

        inicio = time.time()
        log(f"--- [{indice}/{total}] Iniciando: {passagem} ---")

        sucesso = False
        for tentativa in range(1, 4):
            try:
                if registro and registro[0]:
                    roteiro = registro[0]
                    log(f"[{indice}/{total}] Etapa 1/3: Roteiro já existe no banco ({len(roteiro)} chars). Gerando MP3 + legenda...")
                else:
                    log(f"[{indice}/{total}] Etapa 1/3: Solicitando texto ao Gemini (tentativa {tentativa}/3)...")
                    tarefa_gemini = asyncio.create_task(
                        asyncio.wait_for(
                            asyncio.to_thread(gerar_roteiro_explicado, passagem),
                            timeout=120,
                        )
                    )
                    roteiro = await mostrar_progresso_vivo("Gerando texto no Gemini", tarefa_gemini)
                    log(f"[{indice}/{total}] Etapa 2/3: Texto recebido ({len(roteiro)} caracteres). Sintetizando voz MP3 + legenda...")

                tarefa_tts = asyncio.create_task(
                    asyncio.wait_for(
                        gerar_audio_com_legenda(roteiro, voz_tts, caminho_mp3, caminho_legenda),
                        timeout=120,
                    )
                )
                await mostrar_progresso_vivo("Convertendo áudio e legenda no Edge-TTS", tarefa_tts)

                agora = datetime.now().strftime("%d/%m/%Y %H:%M")
                cursor.execute(
                    """
                    INSERT OR REPLACE INTO estudos (slug, passagem, voz, roteiro, arquivo_mp3, criado_em)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (slug, passagem, VOZ_ESCOLHIDA, roteiro, nome_mp3, agora),
                )
                conn.commit()
                duracao = round(time.time() - inicio, 1)
                log(f"[{indice}/{total}] Etapa 3/3: CONCLUÍDO em {duracao}s -> {nome_mp3} + {nome_legenda}")
                sucesso = True
                break

            except asyncio.TimeoutError:
                log(f"[{indice}/{total}] Tempo esgotado na tentativa {tentativa}/3. Aguardando 15s...")
                await asyncio.sleep(15)
            except Exception as e:
                erro_str = str(e)
                if "PerDay" in erro_str or "daily" in erro_str.lower():
                    log(f"[{indice}/{total}] Cota DIÁRIA atingida! Rode novamente amanhã para continuar de onde parou.")
                    conn.close()
                    return
                if "429" in erro_str or "RESOURCE_EXHAUSTED" in erro_str:
                    log(f"[{indice}/{total}] Limite por minuto (429) na tentativa {tentativa}/3. Aguardando 65s...")
                    await asyncio.sleep(65)
                elif any(x in erro_str for x in ("503", "UNAVAILABLE", "500", "DEADLINE_EXCEEDED")):
                    log(f"[{indice}/{total}] Servidor ocupado na tentativa {tentativa}/3. Aguardando 20s...")
                    await asyncio.sleep(20)
                else:
                    log(f"[{indice}/{total}] Erro em {passagem}: {e}")
                    break

        if sucesso and indice < total:
            log(f"Pausa de segurança de {PAUSA_ENTRE_ESTUDOS}s antes do próximo capítulo...")
            await asyncio.sleep(PAUSA_ENTRE_ESTUDOS)

    conn.close()
    log("Processamento finalizado!")


if __name__ == "__main__":
    asyncio.run(processar_novo_testamento())