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
    """Verifica si el partido tiene datos suficientes y está en juego."""
    # 1. Filtro de estado: descartar partidos finalizados o no deseados
    estado = partido.get("Tiempo/Estado", "").upper()
    estados_excluidos = ["FINALIZADO", "FIN", "FT", "APL.", "POSTP."]
    
    if any(excluido in estado for excluido in estados_excluidos):
        return False

    # 2. Filtro de marcador existente
    marcador = partido.get("Marcador", "")
    if not marcador or marcador == "- - -":
        return False

    # 3. Filtro OBLIGATORIO: Debe tener al menos una estadística principal registrada
    #stats = partido.get("Stats", {})
    #if not stats or len(stats) == 0:
        #return False

    # 3. Filtro de cuotas o estadísticas
    if not partido.get("Stats") and partido.get("Cuotas") == "- - -":
        return False

    return True


    # 3. Filtro OBLIGATORIO: Contener al menos una métrica clave relevante
    #stats = partido.get("Stats", {})
    #if not stats:
        #return False

    #metricas_clave = ["posesión", "posesion", "goles esperados", "xg", "ocasiones", "córneres", "corner"]
    #claves_existentes = " ".join(stats.keys()).lower()

    #tiene_metrica_principal = any(m in claves_existentes for m in metricas_clave)
    #if not tiene_metrica_principal:
        #return False
    
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

def parsear_bloque_estadisticas(soup_bloque) -> dict:
    """Parsea el bloque de estadísticas permitiendo valores faltantes (-)."""
    stats = {}

    # 1. Filas métricas estándar (xG, Posesión, Grandes ocasiones, Toques, etc.)
    for fila in soup_bloque.select('div[data-testid="wcl-statistics"]'):
        etiqueta_el = fila.select_one(".wcl-label_sO4bA span, [data-testid='wcl-scores-simple-text-01'], .wcl-name_2lXWg")
        if not etiqueta_el:
            continue

        nombre = etiqueta_el.get_text(strip=True)
        val_l_el = fila.select_one(".wcl-labelRow_42JBQ > div:first-child .wcl-value_Ywp3J")
        val_v_el = fila.select_one(".wcl-awayValue_smmfR .wcl-value_Ywp3J")

        # Asigna el valor o '-' si no existe
        stats[f"{nombre} (L)"] = val_l_el.get_text(strip=True) if val_l_el else "-"
        stats[f"{nombre} (V)"] = val_v_el.get_text(strip=True) if val_v_el else "-"

    # 2. Remates fuera y Remates a puerta (bloque de portería)
    shot_container = soup_bloque.select_one('[class*="shotOnTargetStats_"]')
    if shot_container:
        for sub_class in ['[class*="offTargetBar_"]', '[class*="onTargetBar_"]']:
            barra = shot_container.select_one(sub_class)
            if barra:
                label_el = barra.select_one('[class*="wcl-label_"]')
                vals = barra.select('[class*="wcl-value_"]')
                if label_el:
                    nombre = label_el.get_text(strip=True)
                    stats[f"{nombre} (L)"] = vals[0].get_text(strip=True) if len(vals) > 0 else "-"
                    stats[f"{nombre} (V)"] = vals[-1].get_text(strip=True) if len(vals) > 1 else "-"

    # 3. Córneres, Tarjetas amarillas y rojas (Badges SVG)
    for badge in soup_bloque.select('[class*="incidentValueBadge_"]'):
        svg = badge.select_one("svg")
        if not svg:
            continue

        test_id = svg.get("data-testid", "").lower()
        tipo = None
        if "corner" in test_id:
            tipo = "Córneres"
        elif "yellow-card" in test_id or "yellow" in test_id:
            tipo = "Tarjetas amarillas"
        elif "red-card" in test_id or "red" in test_id:
            tipo = "Tarjetas rojas"

        if tipo:
            spans = [s.get_text(strip=True) for s in badge.select("span") if s.get_text(strip=True)]
            # Si solo hay un span reportado o ninguno, se asigna '-' según corresponda
            stats[f"{tipo} (L)"] = spans[0] if len(spans) >= 1 else "-"
            stats[f"{tipo} (V)"] = spans[-1] if len(spans) >= 2 else "-"

    return stats


def formatear_mensaje_partido(reg: dict) -> str:
    """Formatea el mensaje. Muestra métricas clave con '-' si no existen en el partido."""
    stats = reg.get("Stats", {})
    
    # Lista de métricas prioritarias que quieres ver siempre en el reporte
    metricas_fijas = [
        "Posesión de balón",
        "Goles esperados (xG)",
        "Remates a puerta",
        "Remates fuera",
        "Córneres",
        "Tarjetas amarillas"
    ]
    
    lineas = []
    metricas_procesadas = set()

    # 1. Procesar primero las métricas fijas asegurando formato L | V
    for m in metricas_fijas:
        # Busca coincidencia exacta o parcial (ej. 'Posesión')
        key_l = next((k for k in stats.keys() if m.lower() in k.lower() and "(L)" in k), None)
        key_v = next((k for k in stats.keys() if m.lower() in k.lower() and "(V)" in k), None)

        val_l = stats.get(key_l, "-") if key_l else "-"
        val_v = stats.get(key_v, "-") if key_v else "-"

        lineas.append(f"• <b>{m}:</b> {val_l} | {val_v}")
        if key_l: metricas_procesadas.add(key_l.replace(" (L)", ""))
        if key_v: metricas_procesadas.add(key_v.replace(" (V)", ""))

    # 2. Agregar cualquier otra métrica adicional que haya llegado y no esté en las fijas
    for k, v in stats.items():
        base_name = k.replace(" (L)", "").replace(" (V)", "")
        if base_name not in metricas_procesadas and not any(f.lower() in base_name.lower() for f in metricas_fijas):
            val_l = stats.get(f"{base_name} (L)", "-")
            val_v = stats.get(f"{base_name} (V)", "-")
            lineas.append(f"• <b>{base_name}:</b> {val_l} | {val_v}")
            metricas_procesadas.add(base_name)

    stats_texto = "\n\n📊 <b>Estadísticas Principales (L | V):</b>\n" + "\n".join(lineas)

    return (
        f"⚽ <b>ALERTA DE PARTIDO</b>\n\n"
        f"⚔️ <b>Partido:</b> {reg['Partido en Vivo']}\n"
        f"🔢 <b>Marcador:</b> {reg['Marcador']}\n"
        f"⏱ <b>Minuto:</b> {reg['Minuto']} ({reg['Tiempo/Estado']})\n"
        f"📈 <b>Cuotas (1X2):</b> {reg['Cuotas']}"
        f"{stats_texto}"
    )


def extraer_datos_completos(playwright_context, url_base_partido: str) -> dict:
    """Extrae marcador, cuotas 1X2 y navega a la pestaña de estadísticas."""
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
        # 1. Abrir la página principal del partido (vista Resumen donde están las cuotas)
        url_partido = url_base_partido.split("#")[0].rstrip("/") + "/"
        page.goto(url_partido, timeout=30000, wait_until="domcontentloaded")

        # Esperar que cargue el encabezado con marcador
        try:
            page.wait_for_selector("div.detailScore__wrapper", timeout=7000)
        except Exception:
            pass

        # Esperar los botones de cuotas en la pestaña principal
        try:
            page.wait_for_selector("button[data-analytics-bookmaker-id], [data-testid='wcl-oddsValue']", timeout=4000)
            page.wait_for_timeout(800)
        except Exception:
            pass

        soup_resumen = BeautifulSoup(page.content(), "html.parser")

        # Extraer Marcador, Estado y Minuto
        score = soup_resumen.select_one("div.detailScore__wrapper")
        if score:
            datos_partido["Marcador"] = score.get_text(separator=" ", strip=True)

        status = soup_resumen.select_one("span.fixedHeaderDuel__detailStatus")
        if status:
            datos_partido["Tiempo/Estado"] = status.get_text(strip=True)

        minuto = soup_resumen.select_one("span.eventTime")
        if minuto:
            datos_partido["Minuto"] = minuto.get_text(strip=True)

        # Extraer Cuotas 1X2
        botones = soup_resumen.find_all("button", attrs={"data-analytics-bookmaker-id": True})
        valores_cuotas = []
        for btn in botones:
            span = btn.find("span", {"data-testid": "wcl-oddsValue"})
            if span and span.get_text(strip=True):
                valores_cuotas.append(span.get_text(strip=True))
            if len(valores_cuotas) == 3:
                break

        if len(valores_cuotas) >= 3:
            datos_partido["Cuotas"] = f"1: {valores_cuotas[0]} | X: {valores_cuotas[1]} | 2: {valores_cuotas[2]}"

        # 2. CAMBIAR A LA PESTAÑA "ESTADÍSTICAS"
        tab_stats = page.locator('a[data-analytics-alias="match-statistics"], a:has-text("ESTADÍSTICAS"), button:has-text("ESTADÍSTICAS")').first
        if tab_stats.count() > 0:
            try:
                tab_stats.click(force=True)
                page.wait_for_timeout(1000)
            except Exception:
                pass

        # Esperar contenedor de estadísticas
        try:
            page.wait_for_selector('div[data-testid="statGroup"]', timeout=6000)
        except Exception:
            page.wait_for_timeout(1200)

        # 3. Extraer Estadísticas Principales
        soup_stats = BeautifulSoup(page.content(), "html.parser")
        bloque_principal = None

        for grupo in soup_stats.select('div[data-testid="statGroup"]'):
            cabecera = grupo.select_one('[data-testid="wcl-headerSection-text"], [class*="header"]')
            if cabecera and "principal" in cabecera.get_text(strip=True).lower():
                bloque_principal = grupo
                break

        if not bloque_principal:
            bloque_principal = soup_stats.select_one('div[data-testid="statGroup"]')

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

            print(f"Partidos en vivo encontrados: {len(partidos)}. Procesando los primeros...")
            for p_div in partidos[:10]:
                id_p = p_div.get('id').split('_')[-1]
                #stage_el = p_div.select_one(".event__stage--block, [class*='stage']")
                #estado_fila = stage_el.get_text(strip=True).upper() if stage_el else ""
                
                #if "FIN" in estado_fila or "FINALIZADO" in estado_fila:
                    #print(f"Omitiendo partido finalizado: {id_p}")
                    #continue
                    
                h_team = p_div.find("div", class_=lambda c: c and "home" in c.lower() and "participant" in c.lower())
                a_team = p_div.find("div", class_=lambda c: c and "away" in c.lower() and "participant" in c.lower())
                nombre_partido = f"{h_team.get_text(strip=True) if h_team else 'Local'} vs {a_team.get_text(strip=True) if a_team else 'Visitante'}"

                url_partido = f"https://www.flashscore.pe/partido/{id_p}/"
                data = extraer_datos_completos(context, url_partido)

                partido_completo = {
                    "Partido en Vivo": nombre_partido,
                    "Marcador": data["Marcador"],
                    "Cuotas": data["Cuotas"],
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
                    print(f"Descartado: {nombre_partido}")

        except Exception as e:
            print(f"Error durante el escaneo general: {e}")
        finally:
            browser.close()
            print("Escaneo finalizado.")

if __name__ == "__main__":
    ejecutar_escaneo()
