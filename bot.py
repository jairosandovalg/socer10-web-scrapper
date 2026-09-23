import os
import sys
import time
import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

# --- CONFIGURACIÓN DE TELEGRAM ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "8923959866:AAES1dc4LAsedUKUsGR4p5D1SkaMt7nKyes")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "7272170952")

def enviar_alerta_telegram(mensaje: str) -> bool:
    """Envía un mensaje formateado a Telegram mediante la API HTTP."""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": mensaje,
        "parse_mode": "HTML"
    }
    try:
        response = requests.post(url, json=payload, timeout=10)
        return response.status_code == 200
    except Exception as e:
        print(f"Error al enviar a Telegram: {e}")
        return False

def cumple_criterios_alerta(partido: dict) -> bool:
    """Verifica si el partido tiene datos suficientes para enviar alerta."""
    marcador = partido.get("Marcador", "")
    if not marcador or marcador == "- - -":
        return False
    # Si no tiene estadísticas extraídas, puedes decidir si descartarlo o enviarlo
    if not partido.get("Stats"):
        return False
    return True

def formatear_mensaje_partido(reg: dict) -> str:
    """Da formato visual al mensaje con negritas y emojis."""
    stats = reg.get("Stats", {})
    stats_texto = ""
    if stats:
        lineas = []
        metricas_procesadas = set()

        for k, v in stats.items():
            if " (L)" in k:
                metrica = k.replace(" (L)", "")
                val_l = v
                val_v = stats.get(f"{metrica} (V)", "-")
                lineas.append(f"• <b>{metrica}:</b> {val_l} | {val_v}")
                metricas_procesadas.add(metrica)
            elif " (V)" in k:
                metrica = k.replace(" (V)", "")
                if metrica not in metricas_procesadas:
                    val_l = stats.get(f"{metrica} (L)", "-")
                    val_v = v
                    lineas.append(f"• <b>{metrica}:</b> {val_l} | {val_v}")
                    metricas_procesadas.add(metrica)

        if lineas:
            stats_texto = "\n\n📊 <b>Estadísticas Principales (L | V):</b>\n" + "\n".join(lineas)

    return (
        f"⚽ <b>ALERTA DE PARTIDO</b>\n\n"
        f"⚔️ <b>Partido:</b> {reg['Partido en Vivo']}\n"
        f"🔢 <b>Marcador:</b> {reg['Marcador']}\n"
        f"⏱ <b>Minuto:</b> {reg['Minuto']} ({reg['Tiempo/Estado']})"
        f"{stats_texto}"
    )

def parsear_bloque_estadisticas(soup_bloque) -> dict:
    """Parsea el bloque exacto div[data-testid='statGroup'] de Estadísticas Principales."""
    stats = {}

    # 1. Filas métricas estándar (Goles esperados xG, Posesión, Grandes ocasiones, etc.)
    for fila in soup_bloque.select('div[data-testid="wcl-statistics"]'):
        etiqueta_el = fila.select_one(".wcl-label_sO4bA span, [data-testid='wcl-scores-simple-text-01'], .wcl-name_2lXWg")
        val_l_el = fila.select_one(".wcl-labelRow_42JBQ > div:first-child .wcl-value_Ywp3J")
        val_v_el = fila.select_one(".wcl-awayValue_smmfR .wcl-value_Ywp3J")

        if etiqueta_el and val_l_el and val_v_el:
            nombre = etiqueta_el.get_text(strip=True)
            stats[f"{nombre} (L)"] = val_l_el.get_text(strip=True)
            stats[f"{nombre} (V)"] = val_v_el.get_text(strip=True)

    # 2. Remates fuera y Remates a puerta (bloque de portería)
    shot_container = soup_bloque.select_one('[class*="shotOnTargetStats_"]')
    if shot_container:
        # 2.1 Remates fuera
        off_target = shot_container.select_one('[class*="offTargetBar_"]')
        if off_target:
            label_el = off_target.select_one('[class*="wcl-label_"]')
            vals = off_target.select('[class*="wcl-value_"]')
            if label_el and len(vals) >= 2:
                nombre = label_el.get_text(strip=True)
                stats[f"{nombre} (L)"] = vals[0].get_text(strip=True)
                stats[f"{nombre} (V)"] = vals[-1].get_text(strip=True)

        # 2.2 Remates a puerta
        on_target = shot_container.select_one('[class*="onTargetBar_"]')
        if on_target:
            label_el = on_target.select_one('[class*="wcl-label_"]')
            vals = on_target.select('[class*="wcl-value_"]')
            if label_el and len(vals) >= 2:
                nombre = label_el.get_text(strip=True)
                stats[f"{nombre} (L)"] = vals[0].get_text(strip=True)
                stats[f"{nombre} (V)"] = vals[-1].get_text(strip=True)

    # 3. Córneres, Tarjetas amarillas y rojas (Badges SVG inferiores)
    for badge in soup_bloque.select('[class*="incidentValueBadge_"]'):
        spans = [s.get_text(strip=True) for s in badge.select("span") if s.get_text(strip=True)]
        svg = badge.select_one("svg")

        if svg and len(spans) >= 2:
            test_id = svg.get("data-testid", "").lower()

            tipo = None
            if "corner" in test_id:
                tipo = "Córneres"
            elif "yellow-card" in test_id or "yellow" in test_id:
                tipo = "Tarjetas amarillas"
            elif "red-card" in test_id or "red" in test_id:
                tipo = "Tarjetas rojas"

            if tipo:
                stats[f"{tipo} (L)"] = spans[0]
                stats[f"{tipo} (V)"] = spans[-1]

    return stats

def extraer_estadisticas_partido(playwright_context, url_base_partido: str) -> dict:
    """Abre el partido directo en su pestaña de estadísticas y extrae los datos."""
    datos_partido = {
        "Marcador": "- - -",
        "Tiempo/Estado": "-",
        "Minuto": "-",
        "Stats": {}
    }
    page = None
    try:
        page = playwright_context.new_page()
        # Navegar directo a la subpestaña de estadísticas completas del partido
        url_stats = url_base_partido.rstrip("/") + "/#/estadisticas-del-partido/0"
        page.goto(url_stats, timeout=30000, wait_until="domcontentloaded")

        # 1. Esperar al marcador y encabezados principales
        try:
            page.wait_for_selector("div.detailScore__wrapper", timeout=7000)
            soup_header = BeautifulSoup(page.content(), "html.parser")

            score = soup_header.select_one("div.detailScore__wrapper")
            if score:
                datos_partido["Marcador"] = score.get_text(separator=" ", strip=True)

            status = soup_header.select_one("span.fixedHeaderDuel__detailStatus")
            if status:
                datos_partido["Tiempo/Estado"] = status.get_text(strip=True)

            minuto = soup_header.select_one("span.eventTime")
            if minuto:
                datos_partido["Minuto"] = minuto.get_text(strip=True)
        except Exception:
            pass

        # 2. Asegurar que la pestaña de estadísticas esté activa
        try:
            page.wait_for_selector('div[data-testid="statGroup"]', timeout=6000)
        except Exception:
            # Forzar clic en la pestaña ESTADÍSTICAS si aún no cargó
            tab_stats = page.locator('button:has-text("ESTADÍSTICAS"), a:has-text("ESTADÍSTICAS")').first
            if tab_stats.count() > 0:
                tab_stats.click(force=True)
                page.wait_for_timeout(1000)

        # 3. Localizar el bloque 'Estadísticas principales' y extraer
        soup = BeautifulSoup(page.content(), "html.parser")
        bloque_principal = None

        for grupo in soup.select('div[data-testid="statGroup"]'):
            cabecera = grupo.select_one('[data-testid="wcl-headerSection-text"], [class*="header"]')
            if cabecera and "principal" in cabecera.get_text(strip=True).lower():
                bloque_principal = grupo
                break

        if not bloque_principal:
            bloque_principal = soup.select_one('div[data-testid="statGroup"]')

        if bloque_principal:
            datos_partido["Stats"] = parsear_bloque_estadisticas(bloque_principal)

    except Exception as e:
        print(f"Error procesando {url_base_partido}: {e}")
    finally:
        if page:
            page.close()

    return datos_partido

def ejecutar_escaneo():
    print("Iniciando escaneo de Flashscore...")
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"]
        )
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        )
        main = context.new_page()

        try:
            main.goto("https://www.flashscore.pe/", timeout=35000, wait_until="domcontentloaded")
            btn_live = "//div[contains(@class, 'filters__text') and text()='EN DIRECTO']"
            main.wait_for_selector(btn_live, timeout=15000)
            main.locator(btn_live).click()
            main.wait_for_timeout(3000)

            soup = BeautifulSoup(main.content(), "html.parser")
            partidos = soup.find_all("div", id=lambda x: x and x.startswith("g_1_"))

            if not partidos:
                print("No hay partidos en directo actualmente.")
                return

            print(f"Partidos en vivo encontrados: {len(partidos)}. Analizando...")
            for p_div in partidos[:10]:
                id_p = p_div.get('id').split('_')[-1]
                h_team = p_div.find("div", class_=lambda c: c and "home" in c.lower() and "participant" in c.lower())
                a_team = p_div.find("div", class_=lambda c: c and "away" in c.lower() and "participant" in c.lower())
                nombre_partido = f"{h_team.get_text(strip=True) if h_team else 'Local'} vs {a_team.get_text(strip=True) if a_team else 'Visitante'}"

                url_partido = f"https://www.flashscore.pe/partido/{id_p}/"
                data = extraer_estadisticas_partido(context, url_partido)

                partido_completo = {
                    "Partido en Vivo": nombre_partido,
                    "Marcador": data["Marcador"],
                    "Tiempo/Estado": data["Tiempo/Estado"],
                    "Minuto": data["Minuto"],
                    "Stats": data["Stats"]
                }

                if cumple_criterios_alerta(partido_completo):
                    print(f"Enviando alerta a Telegram de: {nombre_partido}")
                    mensaje = formatear_mensaje_partido(partido_completo)
                    enviado = enviar_alerta_telegram(mensaje)
                    if enviado:
                        print("-> Alerta enviada con éxito.")
                    else:
                        print("-> Falló el envío a Telegram.")
                else:
                    print(f"Partido descartado (sin marcador o sin estadísticas disponibles): {nombre_partido}")

        except Exception as e:
            print(f"Error durante el escaneo general: {e}")
        finally:
            browser.close()
            print("Escaneo finalizado.")

if __name__ == "__main__":
    ejecutar_escaneo()
