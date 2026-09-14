import sys
import os
import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

# --- CONFIGURACIÓN DE TELEGRAM ---
# Se leen desde Secrets de GitHub por seguridad (o valores por defecto)
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
    """
    ESPACIO PARA TUS FILTROS:
    Modifica esta función para definir cuándo debe enviarse una alerta.
    Retorna True si cumple las condiciones, False en caso contrario.
    """
    minuto_str = partido.get("Minuto", "").replace("'", "").strip()
    marcador = partido.get("Marcador", "")
    stats = partido.get("Stats", {})

    # Ejemplo de filtro básico (actualmente activado para que pase todo lo válido):
    # Condición actual: que el partido tenga marcador válido detectado
    if marcador == "- - -" or not marcador:
        return False

    # EJEMPLOS DE FILTROS QUE PODRÁS DESCOMENTAR:
    # 1. Filtrar por minuto:
    # if minuto_str.isdigit() and int(minuto_str) >= 70:
    #     return True

    # 2. Filtrar por tiros a puerta (si existen en las stats extraídas):
    # tiros_local = int(stats.get("Tiros a puerta (L)", 0))
    # if tiros_local >= 4:
    #     return True

    return True

def formatear_mensaje_partido(reg: dict) -> str:
    """Da formato visual al mensaje con negritas y emojis."""
    stats = reg.get("Stats", {})
    stats_texto = ""
    if stats:
        stats_lineas = [f"• {k}: {v}" for k, v in stats.items()]
        stats_texto = "\n\n📊 <b>Estadísticas:</b>\n" + "\n".join(stats_lineas[:8])

    return (
        f"⚽ <b>ALERTA DE PARTIDO</b>\n\n"
        f"⚔️ <b>Partido:</b> {reg['Partido en Vivo']}\n"
        f"🔢 <b>Marcador:</b> {reg['Marcador']}\n"
        f"⏱ <b>Minuto:</b> {reg['Minuto']} ({reg['Tiempo/Estado']})\n"
        f"📈 <b>Cuotas (1X2):</b> {reg['Cuotas']}"
        f"{stats_texto}"
    )

def extraer_estadisticas_partido(playwright_context, url_partido: str) -> dict:
    datos_partido = {
        "Marcador": "- - -",
        "Cuotas": "- - -",
        "Tiempo/Estado": "-",
        "Minuto": "-",
        "Stats": {}
    }
    page = None
    try:
        page = playwright_context.new_page()
        page.goto(url_partido, timeout=25000, wait_until="domcontentloaded")
        page.wait_for_selector("div.detailScore__wrapper", timeout=10000)

        try:
            page.wait_for_selector("button[data-analytics-bookmaker-id='660']", timeout=4000)
            page.wait_for_timeout(1000)
        except Exception:
            pass

        soup = BeautifulSoup(page.content(), "html.parser")

        score = soup.select_one("div.detailScore__wrapper")
        if score:
            datos_partido["Marcador"] = score.get_text(strip=True)

        status = soup.select_one("span.fixedHeaderDuel__detailStatus")
        if status:
            datos_partido["Tiempo/Estado"] = status.get_text(strip=True)

        minuto = soup.select_one("span.eventTime")
        if minuto:
            datos_partido["Minuto"] = minuto.get_text(strip=True)

        botones = soup.find_all("button", {"data-analytics-bookmaker-id": "660"})
        valores = []
        for btn in botones:
            span = btn.find("span", {"data-testid": "wcl-oddsValue"})
            if span:
                valores.append(span.get_text(strip=True))

        if len(valores) >= 3:
            datos_partido["Cuotas"] = f"1:{valores[0]} X:{valores[1]} 2:{valores[2]}"

        selector_boton = "//button[@role='tab' and contains(., 'Estadísticas')]"
        if page.locator(selector_boton).count() > 0:
            page.locator(selector_boton).first.click(force=True)
            page.wait_for_timeout(1000)
            soup_s = BeautifulSoup(page.content(), "html.parser")
            for fila in soup_s.find_all("div", {"data-testid": "wcl-statistics"}):
                cat = fila.find("div", {"data-testid": "wcl-statistics-category"})
                if cat:
                    h = fila.find("div", class_=lambda x: x and 'wcl-homeValue' in x)
                    v = fila.find("div", class_=lambda x: x and 'wcl-awayValue' in x)
                    datos_partido["Stats"][f"{cat.get_text(strip=True)} (L)"] = h.get_text(strip=True) if h else "0"
                    datos_partido["Stats"][f"{cat.get_text(strip=True)} (V)"] = v.get_text(strip=True) if v else "0"
    except Exception as e:
        print(f"Error procesando {url_partido}: {e}")
    finally:
        if page:
            page.close()
    return datos_partido

def ejecutar_escaneo():
    print("Iniciando escaneo de Flashscore...")
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage", "--disable-gpu"]
        )
        context = browser.new_context(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")
        main = context.new_page()

        try:
            main.goto("https://www.flashscore.pe/", timeout=30000)
            btn_live = "//div[contains(@class, 'filters__text') and text()='EN DIRECTO']"
            main.wait_for_selector(btn_live, timeout=15000)
            main.locator(btn_live).click()
            main.wait_for_timeout(3000)

            soup = BeautifulSoup(main.content(), "html.parser")
            partidos = soup.find_all("div", id=lambda x: x and x.startswith("g_1_"))

            if not partidos:
                print("No hay partidos en directo actualmente.")
                browser.close()
                return

            print(f"Partidos encontrados: {len(partidos)}. Analizando los 10 primeros...")
            for p_div in partidos[:10]:
                id_p = p_div.get('id').split('_')[-1]
                h_team = p_div.find("div", class_=lambda c: c and "home" in c.lower() and "participant" in c.lower())
                a_team = p_div.find("div", class_=lambda c: c and "away" in c.lower() and "participant" in c.lower())
                nombre_partido = f"{h_team.get_text(strip=True) if h_team else 'Local'} vs {a_team.get_text(strip=True) if a_team else 'Visitante'}"

                url = f"https://www.flashscore.pe/partido/{id_p}/#/resumen/estadisticas"
                data = extraer_estadisticas_partido(context, url)

                partido_completo = {
                    "Partido en Vivo": nombre_partido,
                    "Marcador": data["Marcador"],
                    "Cuotas": data["Cuotas"],
                    "Tiempo/Estado": data["Tiempo/Estado"],
                    "Minuto": data["Minuto"],
                    "Stats": data["Stats"]
                }

                # Verificación de filtros
                if cumple_criterios_alerta(partido_completo):
                    print(f"Alerta válida: {nombre_partido}. Enviando a Telegram...")
                    mensaje = formatear_mensaje_partido(partido_completo)
                    enviar_alerta_telegram(mensaje)
                else:
                    print(f"Descartado por filtros: {nombre_partido}")

        except Exception as e:
            print(f"Error durante el escaneo general: {e}")
        finally:
            browser.close()
            print("Escaneo finalizado.")

if __name__ == "__main__":
    ejecutar_escaneo()
