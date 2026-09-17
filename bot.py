import sys
import os
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
    marcador = partido.get("Marcador", "")
    if marcador == "- - -" or not marcador:
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
        page.goto(url_partido, timeout=30000, wait_until="domcontentloaded")
        
        # 1. Esperar al marcador
        try:
            page.wait_for_selector("div.detailScore__wrapper", timeout=8000)
        except Exception:
            pass

        # 2. ESPERA Y EXTRACCIÓN DE CUOTAS
        try:
            page.wait_for_selector("button[data-analytics-bookmaker-id='660']", timeout=4000)
            page.wait_for_timeout(1200)
        except Exception:
            pass

        soup = BeautifulSoup(page.content(), "html.parser")

        score = soup.select_one("div.detailScore__wrapper")
        if score:
            datos_partido["Marcador"] = score.get_text(separator=" ", strip=True)

        status = soup.select_one("span.fixedHeaderDuel__detailStatus")
        if status:
            datos_partido["Tiempo/Estado"] = status.get_text(strip=True)

        minuto = soup.select_one("span.eventTime")
        if minuto:
            datos_partido["Minuto"] = minuto.get_text(strip=True)

        # Cuotas
        botones = soup.find_all("button", {"data-analytics-bookmaker-id": "660"})
        valores = []
        for btn in botones:
            span = btn.find("span", {"data-testid": "wcl-oddsValue"})
            if span and span.get_text(strip=True):
                valores.append(span.get_text(strip=True))

        if len(valores) < 3:
            botones_genericos = soup.find_all("button", attrs={"data-analytics-bookmaker-id": True})
            for btn in botones_genericos:
                span = btn.find("span", {"data-testid": "wcl-oddsValue"})
                if span and span.get_text(strip=True):
                    valores.append(span.get_text(strip=True))
                if len(valores) == 3:
                    break

        if len(valores) >= 3:
            datos_partido["Cuotas"] = f"1:{valores[0]} X:{valores[1]} 2:{valores[2]}"

        # 3. NAVEGAR A LA PESTAÑA "ESTADÍSTICAS"
        selector_stats = 'a[data-analytics-alias="match-statistics"], a[role="tab"]:has-text("Estadísticas")'
        tab_stats = page.locator(selector_stats)
        if tab_stats.count() > 0:
            try:
                clases = tab_stats.first.get_attribute("class") or ""
                if "active" not in clases:
                    tab_stats.first.click(force=True)
                    page.wait_for_timeout(1000)
            except Exception:
                pass

        # Sub-pestaña "Partido" acumulado (alias 74)
        tab_partido = page.locator('a[data-analytics-alias="74"], a:has-text("Partido")')
        if tab_partido.count() > 0:
            try:
                clases_sub = tab_partido.first.get_attribute("class") or ""
                if "active" not in clases_sub:
                    tab_partido.first.click(force=True)
                    page.wait_for_timeout(600)
            except Exception:
                pass

        try:
            page.wait_for_selector('[data-testid="statGroup"], [data-testid="wcl-statistics"], .tabContent__match-statistics', timeout=5000)
        except Exception:
            page.wait_for_timeout(1000)

        # 4. EXTRAER EXCLUSIVAMENTE LAS ESTADÍSTICAS PRINCIPALES
        soup_s = BeautifulSoup(page.content(), "html.parser")
        
        primer_grupo = None
        for grupo in soup_s.select('div[data-testid="statGroup"]'):
            titulo = grupo.select_one('[data-testid="wcl-headerSection-text"]')
            if titulo and "principal" in titulo.get_text(strip=True).lower():
                primer_grupo = grupo
                break

        if not primer_grupo:
            grupos = soup_s.select('div[data-testid="statGroup"]')
            if grupos:
                primer_grupo = grupos[0]

        # EXTRACCIÓN LIMITADA ESTRICTAMENTE AL PRIMER GRUPO
        if primer_grupo:
            # A. Filas estándar (xG, Posesión, Grandes ocasiones, Toques en el área, etc.)
            for fila in primer_grupo.select('[data-testid="wcl-statistics"]'):
                nombre_el = (
                    fila.select_one('[class*="wcl-name_"]') or
                    fila.select_one('[class*="wcl-label_"]') or
                    fila.select_one('[data-testid*="category"]')
                )
                vals = [v.get_text(strip=True) for v in fila.select('[class*="wcl-value_"]') if v.get_text(strip=True)]
                if nombre_el and len(vals) >= 2:
                    nombre = nombre_el.get_text(strip=True)
                    if nombre and f"{nombre} (L)" not in datos_partido["Stats"]:
                        datos_partido["Stats"][f"{nombre} (L)"] = vals[0]
                        datos_partido["Stats"][f"{nombre} (V)"] = vals[-1]

            # B. Barras de remates (Remates a puerta, fuera, etc.)
            for shot_bar in primer_grupo.select('[class*="wcl-shotOnTargetStats_"]'):
                nombre_el = shot_bar.select_one('[class*="wcl-label_"]')
                vals = [v.get_text(strip=True) for v in shot_bar.select('[class*="wcl-value_"]') if v.get_text(strip=True)]
                if nombre_el and len(vals) >= 2:
                    nombre = nombre_el.get_text(strip=True)
                    if nombre and f"{nombre} (L)" not in datos_partido["Stats"]:
                        datos_partido["Stats"][f"{nombre} (L)"] = vals[0]
                        datos_partido["Stats"][f"{nombre} (V)"] = vals[-1]

            # C. Córneres y tarjetas (Badges SVG)
            for badge in primer_grupo.select('[class*="wcl-incidentValueBadge_"]'):
                spans = [s.get_text(strip=True) for s in badge.find_all("span") if s.get_text(strip=True)]
                svg = badge.find("svg")
                if len(spans) >= 2 and svg:
                    svg_testid = (svg.get("data-testid") or "").lower()
                    svg_class = " ".join(svg.get("class", [])).lower()

                    nombre = None
                    if "corner" in svg_testid or "corner" in svg_class:
                        nombre = "Córneres"
                    elif "yellow" in svg_testid or "yellow" in svg_class:
                        nombre = "Tarjetas amarillas"
                    elif "red" in svg_testid or "red" in svg_class:
                        nombre = "Tarjetas rojas"
                    elif "card" in svg_testid or "card" in svg_class:
                        nombre = "Tarjetas"

                    if nombre and f"{nombre} (L)" not in datos_partido["Stats"]:
                        datos_partido["Stats"][f"{nombre} (L)"] = spans[0]
                        datos_partido["Stats"][f"{nombre} (V)"] = spans[-1]

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
