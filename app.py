# LGePR Data Cleaner v11.10 (Debug Output Fix)
# FIX: Usunięto wyświetlanie "None" i dokumentacji DeltaGenerator
# - Poprawiono linię st.info/st.write w sekcji kroków (s1, s2, s3, s4)
# - Usunięto problematyczne wywołania st.write() które printowały None

import streamlit as st
import pandas as pd
import re
import io
import time
import json
import os
import requests
from bs4 import BeautifulSoup
from datetime import datetime
import openpyxl

# Biblioteki AgGrid
try:
    from st_aggrid import AgGrid, GridOptionsBuilder, GridUpdateMode, JsCode, DataReturnMode
    AGGRID_AVAILABLE = True
except ImportError:
    AGGRID_AVAILABLE = False

# Próba importu newspaper
try:
    from newspaper import Article
    NEWSPAPER_AVAILABLE = True
except ImportError:
    NEWSPAPER_AVAILABLE = False

# ─────────────────────────────────────────────
# 1. KONFIGURACJA
# ─────────────────────────────────────────────
st.set_page_config(page_title="LGePR Cleaner", page_icon="🧹", layout="wide")

hide_ui_css = """
<style>
#MainMenu {visibility: hidden;}
footer {visibility: hidden;}
header {visibility: hidden;}
.stDeployButton {display:none;}
div[data-testid="stDecoration"] {display:none;}
</style>
"""
st.markdown(hide_ui_css, unsafe_allow_html=True)

# ─────────────────────────────────────────────
# 2. DEFINICJE I REGUŁY
# ─────────────────────────────────────────────
TITLE_MAX_LEN = 120
QUOTE_MAX_LEN = 120
ID_TITLE_CHARS = 30

SANITIZATION_PATTERN = re.compile(r'[.:!?"\'()\[\]/\\$€£zł\-–—]')
YEAR_PATTERN = re.compile(r'\b2026\b')

FINAL_OUTPUT_ORDER = [
    'zrodlo', 'tytul', 'zasieg', 'data',
    'ENG Title', 'Division', 'Product', 'ESG', 'M/Z',
    'Links', 'Quote', 'LG', 'Exclusive', 'Photo',
    'clean_title', 'clean_quote', 'ID_MATCH', '_media_status', 'PR Value'
]

VALIDATION_RULES = {
    "Division": ["Corporate", "HS", "MS", "VS", "ES"],
    "Photo": ["None", "LGE logo", "product", "personnel"],
    "Exclusive": ["Exclusive", "33", "50", "66"],
    "LG": ["N/A", "LG Electronics"]
}

PRODUCT_RULES = {
    "Corporate": ["Corporate/Brand", "Top Management", "Finance", "MC", "Others"],
    "HS": ["Refrigerator", "Washer/Dryer", "Cooking Appliance", "Vacuum Cleaner", "Styler", "Air Solution", "Others"],
    "MS": ["LCD TV", "Audio", "OLED TV", "Signage", "PC", "Projector", "Monitor", "Others"],
    "VS": ["VS"],
    "ES": ["SAC", "RAC", "AirCare", "Chiller", "Others"]
}

# ─────────────────────────────────────────────
# 3. OBSŁUGA SEKRETÓW
# ─────────────────────────────────────────────
def get_secret(key, default=None):
    try: return st.secrets.get(key, default)
    except: return default

def check_password():
    if "password_correct" not in st.session_state:
        st.session_state.password_correct = False
    if st.session_state.password_correct:
        return True
    
    st.markdown("### 🔒 Dostęp autoryzowany")
    pwd = st.text_input("Hasło:", type="password")
    if st.button("Zaloguj"):
        secret_pwd = get_secret("APP_PASSWORD", "admin123")
        if pwd == secret_pwd:
            st.session_state.password_correct = True
            st.rerun()
        else:
            st.error("Błędne hasło")
    return False

if not check_password():
    st.stop()

def get_cloud_config():
    api_key = get_secret("OPENAI_API_KEY", "")
    raw_media_list = get_secret("MEDIA_LIST", [])
    if isinstance(raw_media_list, str): 
        raw_media_list = [x.strip() for x in raw_media_list.split(',')]
    
    normalized_set = set()
    for m in raw_media_list:
        clean_m = normalize_domain(m).lower()
        if clean_m: normalized_set.add(clean_m)
    return api_key, normalized_set

# ─────────────────────────────────────────────
# 4. POMOCNIKI
# ─────────────────────────────────────────────
def normalize_domain(val):
    if pd.isna(val): return ""
    val_str = str(val).strip()
    if '.' not in val_str: return val_str
    
    u = val_str.lower()
    u = re.sub(r'^https?://', '', u)
    u = re.sub(r'^www\.', '', u)
    if u.endswith('/'): u = u[:-1]
    
    if u.endswith('.onet.pl') or u == 'onet.pl': return 'onet.pl'
    if u.endswith('.wp.pl') or u == 'wp.pl': return 'wp.pl'
    if u.endswith('.gazeta.pl') or u == 'gazeta.pl': return 'gazeta.pl'
    if u.endswith('.interia.pl') or u == 'interia.pl': return 'interia.pl'
    if u.endswith('.infor.pl') or u == 'infor.pl': return 'infor.pl'
    
    mapping = {
        'komputerswiat.pl': 'onet.pl', 'auto-swiat.pl': 'onet.pl', 'businessinsider.com.pl': 'onet.pl', 'plejada.pl': 'onet.pl', 'medonet.pl': 'onet.pl',
        'benchmark.pl': 'wp.pl', 'gadzetomania.pl': 'wp.pl', 'dobreprogramy.pl': 'wp.pl', 'pudelek.pl': 'wp.pl', 'money.pl': 'wp.pl', 'autokult.pl': 'wp.pl',
        'next.gazeta.pl': 'gazeta.pl', 'sport.pl': 'gazeta.pl', 'plotek.pl': 'gazeta.pl', 'moto.pl': 'gazeta.pl',
        'pomponik.pl': 'interia.pl', 'swiatseriali.interia.pl': 'interia.pl'
    }
    return mapping.get(u, u)

def has_value(val):
    if val is None: return False
    try:
        if pd.isna(val): return False
    except: pass
    s = str(val).strip()
    if s == "" or s.lower() in ["nan", "none", "[no_content]", "[ai_fail]", "[json_err]", "[no_img]", "error getting image"] or "error" in s.lower(): 
        return False
    return True

def validate_val(val, allowed_list):
    if not has_value(val): return False
    return str(val).strip() in [str(x) for x in allowed_list]

def enforce_strict_rules(key, value, context_division=None):
    val_str = str(value).strip()
    if key == "Division":
        if val_str in VALIDATION_RULES["Division"]: return val_str
        return "[CHECK]"
    if key == "Product":
        allowed = []
        if context_division and context_division in PRODUCT_RULES:
            allowed = PRODUCT_RULES[context_division]
        else:
            for p_list in PRODUCT_RULES.values(): allowed.extend(p_list)
        if val_str in allowed: return val_str
        if "LED TV" in val_str: return "LCD TV"
        if "Vrand" in val_str: return "Corporate/Brand"
        return "Others" if "Others" in allowed else "[CHECK]"
    if key == "Photo":
        return val_str if val_str in VALIDATION_RULES["Photo"] else "None"
    if key == "Exclusive":
        return val_str if str(val_str) in [str(x) for x in VALIDATION_RULES["Exclusive"]] else "33"
    return val_str

def clean_text(t, l):
    if pd.isna(t): return ""
    x = str(t).strip()
    x = YEAR_PATTERN.sub("2026r", x)
    x = SANITIZATION_PATTERN.sub(" ", x)
    x = re.sub(r'\s+', ' ', x).strip()
    if len(x) > l:
        x = x[:l]
        last_space = x.rfind(' ')
        if last_space != -1: x = x[:last_space]
    return x.strip()

def scrape_article_data(url):
    if not str(url).startswith('http'): url = 'https://' + str(url)
    result = {"text": "", "image_url": None}
    
    if NEWSPAPER_AVAILABLE:
        try:
            a = Article(url)
            a.download()
            a.parse()
            result["text"] = a.text[:4000] if a.text else ""
            result["image_url"] = a.top_image
            if result["text"]: return result
        except: pass

    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
        response = requests.get(url, headers=headers, timeout=10)
        soup = BeautifulSoup(response.content, 'html.parser')
        paragraphs = soup.find_all('p')
        text_content = " ".join([p.get_text() for p in paragraphs])
        
        image_url = None
        meta_img = soup.find('meta', property='og:image')
        if meta_img: image_url = meta_img.get('content')
        
        result["text"] = text_content[:4000] if text_content else ""
        if not result["image_url"]: result["image_url"] = image_url
    except: pass
    
    return result

def extract_specific_columns(f, sheet, media_list_set) -> pd.DataFrame:
    wb = openpyxl.load_workbook(f, data_only=False)
    ws = wb[sheet]
    headers = {str(ws.cell(1, c).value).strip(): c for c in range(1, ws.max_column+1) if ws.cell(1, c).value}
    
    data = []
    for r in range(2, ws.max_row+1):
        src_val = ws.cell(r, headers.get('source', 4)).value
        tit_val = ws.cell(r, headers.get('title', 5)).value
        rea_val = ws.cell(r, headers.get('reach', 7)).value
        dat_val = ws.cell(r, headers.get('date of service', 8)).value
        div_val = ws.cell(r, headers.get('Division', 10)).value
        prod_val = ws.cell(r, 11).value
        excl_val = ws.cell(r, 12).value
        phot_val = ws.cell(r, 13).value
        
        link = ""
        c = ws.cell(r, headers.get('source', 4))
        if c.hyperlink and c.hyperlink.target: link = c.hyperlink.target
        elif isinstance(c.value, str) and c.value.startswith('http'): link = c.value
        
        clean_src_display = normalize_domain(src_val)
        check_val = clean_src_display.lower()
        stat = "OK" if media_list_set and check_val in media_list_set else "BRAK"
        if not media_list_set: stat = "N/A"
        
        lg_calc = "LG Electronics" if "LG" in str(tit_val).upper() else "N/A"
        day = str(dat_val)
        try: day = str(pd.to_datetime(dat_val).day)
        except: pass

        row = {
            'zrodlo': clean_src_display, 'tytul': tit_val, 'zasieg': rea_val,
            'data': day, '_orig_date': dat_val, 
            'Links': re.sub(r'^https?://', '', str(link).strip()) if link else "",
            'Division': div_val, 'Product': prod_val, 'Exclusive': excl_val, 'Photo': phot_val,
            'ENG Title': "", 'Quote': "", 'ESG': "", 'M/Z': "", 'LG': lg_calc, '_media_status': stat, 'PR Value': ""
        }
        data.append(row)
    wb.close()
    return pd.DataFrame(data)

def generate_id_match(row):
    src = str(row.get('zrodlo', '')).strip()
    tit = str(row.get('clean_title', '') or row.get('tytul', ''))[:ID_TITLE_CHARS].strip()
    try: d = pd.to_datetime(row.get('_orig_date')).strftime("%Y%m%d")
    except: d = str(row.get('_orig_date', ''))[:8].replace('-','')
    return f"{src}|{tit}|{d}"

def merge_datasets(clean_df, report_df):
    # 1. Autodetekcja kolumn w Raporcie
    title_col = 'Headline'
    if 'Headline' not in report_df.columns:
        if 'Tytuł' in report_df.columns: title_col = 'Tytuł'
        elif 'Title' in report_df.columns: title_col = 'Title'
    
    date_col = 'Published'
    if 'Published' not in report_df.columns:
        if 'Data' in report_df.columns: date_col = 'Data'
        elif 'Date' in report_df.columns: date_col = 'Date'
        
    pr_col = 'PR Value'
    if 'PR Value' not in report_df.columns:
        if 'AVE' in report_df.columns: pr_col = 'AVE'

    # Funkcja generująca klucz łączenia
    def create_key(row, media_val, title_val, date_val):
        m = normalize_domain(str(media_val))
        
        # Tytuł: używamy clean_text, aby pozbyć się interpunkcji (ważne dla matchowania!)
        t_clean = clean_text(title_val, 200) 
        t = t_clean.lower().strip()[:30]
        
        d_str = str(date_val).strip()
        if len(d_str) > 10: d_str = d_str[:10] # Bierzemy tylko datę (YYYY-MM-DD)
        
        return f"{m}|{t}|{d_str}"

    # Generowanie kluczy
    # DLA CLEAN: Używamy 'clean_title' (to jest wersja ENG po tłumaczeniu), aby pasowało do Raportu ENG
    clean_df['__merge_key'] = clean_df.apply(
        lambda r: create_key(r, r['zrodlo'], r['clean_title'], r.get('_orig_date', r.get('data'))), 
        axis=1
    )
    
    # DLA REPORT: Używamy wykrytych kolumn (np. Headline, Published)
    report_df['__merge_key'] = report_df.apply(
        lambda r: create_key(r, r.get('Media', r.get('Source')), r.get(title_col), r.get(date_col)), 
        axis=1
    )
    
    # Mapowanie wartości
    pr_map = dict(zip(report_df['__merge_key'], report_df[pr_col]))
    clean_df['PR Value'] = clean_df['__merge_key'].map(pr_map)
    
    clean_df.drop(columns=['__merge_key'], inplace=True)
    report_df.drop(columns=['__merge_key'], inplace=True)
    
    return clean_df

def clean_json_response(raw_resp):
    try:
        start = raw_resp.find('{')
        end = raw_resp.rfind('}') + 1
        if start != -1 and end != -1:
            clean_str = raw_resp[start:end]
            return json.loads(clean_str)
        else: return None
    except: return None

def call_openai_single(prompt, key, model):
    url = "https://api.openai.com/v1/chat/completions"
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {key}"}
    payload = {
        "model": model,
        "messages": [{"role": "system", "content": "You are a Data Analyst."}, {"role": "user", "content": prompt}],
        "temperature": 0.1
    }
    
    for attempt in range(3):
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=25)
            if resp.status_code == 200:
                return resp.json()['choices'][0]['message']['content']
            elif resp.status_code == 429:
                time.sleep(2 * (attempt + 1))
                continue
            else:
                return f"[API_ERROR: {resp.status_code}]"
        except Exception as e:
            if attempt == 2: return f"[CONN_ERR: {str(e)[:20]}]"
            time.sleep(1)
    return "[TIMEOUT]"

def call_openai_vision(prompt, img_url, key):
    url = "https://api.openai.com/v1/chat/completions"
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {key}"}
    payload = {
        "model": "gpt-4o",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": img_url, "detail": "low"}}
                ]
            }
        ],
        "max_tokens": 50
    }
    
    for attempt in range(3):
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=25)
            if resp.status_code == 200:
                return resp.json()['choices'][0]['message']['content']
            elif resp.status_code == 429:
                time.sleep(2 * (attempt + 1))
                continue
            else:
                return f"[API_ERROR: {resp.status_code}]"
        except Exception as e:
            if attempt == 2: return f"[CONN_ERR: {str(e)[:20]}]"
            time.sleep(1)
    return "[TIMEOUT]"

def analyze_row_with_ai(row, api_key):
    needs_div = not has_value(row['Division'])
    needs_prod = not has_value(row['Product'])
    needs_excl = not has_value(row['Exclusive'])
    needs_quote = not has_value(row['Quote'])
    needs_photo = not has_value(row['Photo'])
    needs_eng = not has_value(row['ENG Title']) 
    
    if not any([needs_div, needs_prod, needs_excl, needs_quote, needs_photo, needs_eng]):
        return None

    url = row.get('Links', '')
    scraped = scrape_article_data(url) if url else {"text": "", "image_url": None}
    text_content = scraped.get('text', '')
    img_url = scraped.get('image_url')
    source_text = text_content
    source_note = ""
    orig_title = str(row.get('tytul', ''))

    if not source_text or len(source_text) < 50:
        source_text = orig_title
        source_note = "[TITLE ONLY] "
        
    updates = {}

    if any([needs_div, needs_prod, needs_excl, needs_quote, needs_eng]):
        current_div = row.get('Division', '') if has_value(row['Division']) else ""
        current_prod = row.get('Product', '') if has_value(row['Product']) else ""
        
        constraint_txt = ""
        if current_div: constraint_txt += f" CONSTRAINT: Division is FIXED to '{current_div}'. Select Product ONLY from its list."
        if current_prod: constraint_txt += f" CONSTRAINT: Product is FIXED to '{current_prod}'. Infer Division from it."

        if not source_text or len(source_text) < 5:
             err_msg = "[NO_CONTENT]"
             if needs_div: updates['Division'] = err_msg
             if needs_prod: updates['Product'] = err_msg
             if needs_excl: updates['Exclusive'] = err_msg
             if needs_quote: updates['Quote'] = err_msg
             if needs_eng: updates['ENG Title'] = err_msg
        else:
            prompt = f"""
            Analyze article about LG Electronics. {source_note}
            Original Title: "{orig_title}"
            Product Map: {json.dumps(PRODUCT_RULES)}
            
            Rules:
            1. Identify Division and Product. {constraint_txt}
            2. If NOT about LG (e.g. Chem, Solar), Division='Corporate', Product='Others'.
            3. Exclusive: <33% -> '33', 40-47% -> '50', >60% -> '66', 100% -> 'Exclusive'.
            4. Quote: Extract 1 relevant sentence (max 150 chars) AND TRANSLATE it to US English.
               CONSTRAINT: If the quote contains "LG", keep "LG".
            5. Translate 'Original Title' to US English (field: 'EngTitle').
               CONSTRAINT: If 'Original Title' contains "LG", the 'EngTitle' MUST also contain "LG".
            
            Return JSON: {{ "Division": "...", "Product": "...", "Exclusive": "...", "Quote": "...", "EngTitle": "..." }}
            Text: {source_text[:2500]}
            """
            
            raw_resp = call_openai_single(prompt, api_key, "gpt-4o-mini")
            data = clean_json_response(raw_resp)
            
            if data:
                if needs_div: updates['Division'] = enforce_strict_rules("Division", data.get('Division', ''))
                if needs_prod: updates['Product'] = enforce_strict_rules("Product", data.get('Product', ''), updates.get('Division', current_div))
                if needs_excl: updates['Exclusive'] = enforce_strict_rules("Exclusive", data.get('Exclusive', ''))
                if needs_quote: updates['Quote'] = data.get('Quote', '')
                if needs_eng: updates['ENG Title'] = data.get('EngTitle', '')
            else:
                err_frag = f"[JSON_ERR: {raw_resp[:20]}]"
                if needs_div: updates['Division'] = err_frag
                if needs_prod: updates['Product'] = err_frag
                if needs_excl: updates['Exclusive'] = err_frag
                if needs_quote: updates['Quote'] = err_frag
                if needs_eng: updates['ENG Title'] = err_frag

    if needs_photo:
        if img_url:
            vision_prompt = "What is in this image related to LG? Return ONLY one string: 'LGE logo', 'product', 'personnel', or 'None'."
            raw_vis = call_openai_vision(vision_prompt, img_url, api_key)
            clean_vis = raw_vis.replace("'", "").replace('"', '').replace(".", "").strip()
            updates['Photo'] = enforce_strict_rules("Photo", clean_vis)
        else:
            updates['Photo'] = "None"

    if not updates: return None
    return {"index": row.name, "changes": updates}

# --- AGGRID HELPER ---
def prepare_aggrid_data(df):
    df['clean_title'] = df.apply(lambda r: clean_text(r['ENG Title'] if has_value(r['ENG Title']) else r['tytul'], TITLE_MAX_LEN), axis=1)
    df['clean_quote'] = df['Quote'].apply(lambda x: clean_text(x, QUOTE_MAX_LEN))
    df['ID_MATCH'] = df.apply(generate_id_match, axis=1)
    return df

# ─────────────────────────────────────────────
# 6. GŁÓWNA APLIKACJA
# ─────────────────────────────────────────────
def main():
    st.title("🧹 LGePR Data Cleaner v11.10")

    if not AGGRID_AVAILABLE:
        st.error("❌ Brak biblioteki streamlit-aggrid. Zainstaluj ją komendą: pip install streamlit-aggrid")
        st.stop()

    if 'config_loaded' not in st.session_state:
        secret_key, secret_media = get_cloud_config()
        st.session_state.saved_api_key = secret_key
        st.session_state.media_list = secret_media
        st.session_state.config_loaded = True
        st.session_state.step = 1
        st.session_state.df_work = None
        st.session_state.ai_proposals = None
        st.session_state.grid_key_suffix = 0 

    with st.sidebar:
        st.header("Ustawienia")
        if st.session_state.saved_api_key:
            st.success("✅ Klucz API (Secrets)")
            active_key = st.session_state.saved_api_key
        else:
            active_key = st.text_input("Klucz API (Tymczasowy)", type="password")
        
        st.divider()
        st.header("Media")
        if st.session_state.media_list:
             st.success(f"✅ Baza mediów (Secrets): {len(st.session_state.media_list)}")
        else:
            st.warning("Brak listy mediów w Secrets. Użyj pliku tymczasowego.")

    # ===== FIX: Poprawiona sekcja kroków =====
    # Problem był tutaj - st.info() i st.write() zwracają None, 
    # a użycie ich w wyrażeniu warunkowym powodowało wypisanie "None"
    s1, s2, s3, s4 = st.columns(4)
    curr = st.session_state.step
    
    # Używamy with context manager zamiast inline conditional
    with s1:
        if curr == 1:
            st.info("1. Upload")
        else:
            st.markdown("1. Upload")
    with s2:
        if curr == 2:
            st.info("2. Analiza AI")
        else:
            st.markdown("2. Analiza AI")
    with s3:
        if curr == 3:
            st.info("3. Weryfikacja")
        else:
            st.markdown("3. Weryfikacja")
    with s4:
        if curr == 4:
            st.info("4. Merge")
        else:
            st.markdown("4. Merge")
    # ===== KONIEC FIX =====
    
    st.divider()

    if curr == 1:
        f = st.file_uploader("Wgraj plik roboczy (.xlsx)", type=['xlsx', 'xlsm'])
        if f:
            try:
                wb = openpyxl.load_workbook(f, read_only=True)
                sheets = wb.sheetnames
                wb.close()
                sh = st.selectbox("Arkusz:", sheets)
                if st.button("🚀 Załaduj i Pokaż", type="primary"):
                    f.seek(0)
                    df = extract_specific_columns(f, sh, st.session_state.media_list)
                    st.session_state.df_work = df
                    st.session_state.grid_key_suffix += 1 
                    st.success(f"Wczytano {len(df)} wierszy.")
                    st.rerun()
            except Exception as e:
                st.error(f"Błąd pliku: {e}")
        
        if st.session_state.df_work is not None:
            st.markdown(f"### 📄 Podgląd danych (Cały plik: {len(st.session_state.df_work)} wierszy)")
            st.dataframe(st.session_state.df_work, use_container_width=True, height=500)
            col_btn, _ = st.columns([1, 4])
            with col_btn:
                if st.button("Przejdź do Analizy →", type="primary"):
                    st.session_state.step = 2
                    st.rerun()

    elif curr == 2:
        df = st.session_state.df_work
        st.markdown("### 🧠 Analiza treści, obrazu i tłumaczenie")
        st.info("AI przeanalizuje linki, uzupełni pola, pobierze zdjęcia i PRZETŁUMACZY (Title/Quote) na US English (zachowując 'LG').")
        
        c1, c2 = st.columns([1, 3])
        with c1:
            run_analysis = st.button("▶️ Uruchom Pełną Analizę", type="primary", disabled=not active_key)
        
        if run_analysis:
            progress_bar = st.progress(0)
            status_text = st.empty()
            proposals = []
            total = len(df)
            
            for i, row in df.iterrows():
                status_text.text(f"Analizuję wiersz {i+1}/{total}: {str(row['tytul'])[:30]}...")
                update = analyze_row_with_ai(row, active_key)
                if update:
                    proposals.append(update)
                progress_bar.progress((i + 1) / total)
            
            status_text.success("Analiza zakończona!")
            if proposals:
                st.session_state.ai_proposals = proposals
                st.rerun()
            else:
                st.warning("Wszystko wygląda na uzupełnione lub brak danych do analizy.")

        if st.session_state.ai_proposals:
            st.divider()
            st.markdown(f"**Znaleziono {len(st.session_state.ai_proposals)} sugestii zmian.**")
            
            prop_data = []
            for p in st.session_state.ai_proposals:
                for k, v in p['changes'].items():
                    prop_data.append({
                        "Idx": p['index'],
                        "Wiersz": p['index'] + 2,
                        "Kolumna": k,
                        "Obecnie": df.at[p['index'], k],
                        "Sugestia AI": v
                    })
            
            edited_props = st.data_editor(
                pd.DataFrame(prop_data),
                use_container_width=True,
                disabled=["Wiersz", "Kolumna", "Obecnie"],
                column_config={"Idx": None},
                hide_index=True
            )
            
            if st.button("✅ Zatwierdź zmiany i przejdź dalej"):
                for _, row_p in edited_props.iterrows():
                    st.session_state.df_work.at[row_p['Idx'], row_p['Kolumna']] = row_p['Sugestia AI']
                st.session_state.ai_proposals = None
                st.session_state.step = 3
                st.rerun()
        else:
            if st.button("Pomiń / Dalej →"):
                st.session_state.step = 3
                st.rerun()

    elif curr == 3:
        st.markdown("### 🔍 Weryfikacja i Edycja (AgGrid Live)")
        
        df_prepared = prepare_aggrid_data(st.session_state.df_work)
        cols = [c for c in FINAL_OUTPUT_ORDER if c in df_prepared.columns]
        if '_orig_date' in df_prepared.columns:
             cols.append('_orig_date')
        
        custom_css = {
            ".cell-error": {
                "background-color": "#ffcccc !important",
                "color": "darkred !important",
                "font-weight": "bold !important"
            }
        }
        
        js_division = JsCode(f"""
        {{
            'cell-error': function(params) {{
                const allowed = {json.dumps(VALIDATION_RULES["Division"])};
                let val = params.value;
                if (val === null || val === undefined) val = "";
                val = val.toString().trim();
                return !allowed.includes(val);
            }}
        }}
        """)

        js_product = JsCode(f"""
        {{
            'cell-error': function(params) {{
                const map = {json.dumps(PRODUCT_RULES)};
                let div = params.data.Division;
                if (!div) div = "";
                div = div.toString().trim();
                let val = params.value;
                if (val === null || val === undefined) val = "";
                val = val.toString().trim();
                let allowed = [];
                if (map[div]) {{ allowed = map[div]; }} else {{ Object.values(map).forEach(arr => allowed.push(...arr)); }}
                return !allowed.includes(val);
            }}
        }}
        """)
        
        js_photo = JsCode(f"""
        {{
            'cell-error': function(params) {{
                const allowed = {json.dumps(VALIDATION_RULES["Photo"])};
                let val = params.value;
                if (val === null || val === undefined) val = "";
                val = val.toString().trim();
                return !allowed.includes(val);
            }}
        }}
        """)
        
        js_exclusive = JsCode(f"""
        {{
            'cell-error': function(params) {{
                const allowed = {json.dumps(VALIDATION_RULES["Exclusive"])};
                let val = params.value;
                if (val === null || val === undefined) val = "";
                val = val.toString().trim();
                return !allowed.includes(val);
            }}
        }}
        """)
        
        js_lg = JsCode(f"""
        {{
            'cell-error': function(params) {{
                const allowed = {json.dumps(VALIDATION_RULES["LG"])};
                let val = params.value;
                if (val === null || val === undefined) val = "";
                val = val.toString().trim();
                return !allowed.includes(val);
            }}
        }}
        """)
        
        js_media = JsCode("""
        {
            'cell-error': function(params) {
                let val = params.value;
                if (val === null || val === undefined) val = "";
                val = val.toString().trim();
                return val === 'BRAK';
            }
        }
        """)

        gb = GridOptionsBuilder.from_dataframe(df_prepared[cols])
        gb.configure_default_column(editable=True, resizable=True, wrapText=True, autoHeight=True)
        gb.configure_column('ID_MATCH', editable=False)
        
        if '_orig_date' in df_prepared.columns:
            gb.configure_column('_orig_date', hide=True)
        
        gb.configure_column('Division', cellClassRules=js_division)
        gb.configure_column('Product', cellClassRules=js_product)
        gb.configure_column('Photo', cellClassRules=js_photo)
        gb.configure_column('Exclusive', cellClassRules=js_exclusive)
        gb.configure_column('LG', cellClassRules=js_lg)
        gb.configure_column('_media_status', cellClassRules=js_media)

        gb.configure_grid_options(domLayout='normal', height=600)
        gridOptions = gb.build()

        current_grid_key = f"editor_grid_{st.session_state.grid_key_suffix}"
        
        grid_response = AgGrid(
            df_prepared[cols], 
            gridOptions=gridOptions, 
            custom_css=custom_css, 
            allow_unsafe_jscode=True, 
            update_mode=GridUpdateMode.VALUE_CHANGED,
            data_return_mode=DataReturnMode.FILTERED_AND_SORTED,
            fit_columns_on_grid_load=False,
            enable_enterprise_modules=False,
            key=current_grid_key, 
            reload_data=False 
        )

        updated_df = pd.DataFrame(grid_response['data'])
        
        if not updated_df.equals(st.session_state.df_work):
             st.session_state.df_work = updated_df

        err_count = 0
        for i, row in updated_df.iterrows():
            div = str(row.get('Division', '')).strip()
            if div not in VALIDATION_RULES['Division']:
                err_count += 1
            else:
                allowed = PRODUCT_RULES.get(div, [])
                if str(row.get('Product', '')).strip() not in allowed:
                    err_count += 1
            if str(row.get('Photo', '')).strip() not in VALIDATION_RULES['Photo']:
                err_count += 1
            if str(row.get('Exclusive', '')).strip() not in VALIDATION_RULES['Exclusive']:
                err_count += 1
            if str(row.get('LG', '')).strip() not in VALIDATION_RULES['LG']:
                err_count += 1
            if str(row.get('_media_status', '')).strip() == 'BRAK':
                err_count += 1

        if err_count > 0:
            st.warning(f"⚠️ Znaleziono ok. {err_count} pól do poprawy (podświetlone na czerwono).")
        else:
            st.success("✅ Wszystkie pola wyglądają poprawnie!")

        col_d1, col_d2 = st.columns(2)
        with col_d1:
            b = io.BytesIO()
            with pd.ExcelWriter(b, engine='xlsxwriter') as w:
                st.session_state.df_work.to_excel(w, sheet_name='Dane_Clean', index=False)
            
            st.download_button(
                label="⬇️ Pobierz Czysty Plik", 
                data=b.getvalue(), 
                file_name="LGePR_Clean.xlsx", 
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", 
                type="primary"
            )
        
        with col_d2:
            st.markdown("Masz już plik z PR Value?")
            if st.button("Przejdź do Mergowania →"):
                st.session_state.step = 4
                st.rerun()

    elif curr == 4:
        st.markdown("### 🔗 Łączenie z Raportem PR Value")
        c1, c2 = st.columns(2)
        with c1:
            f_clean = st.file_uploader("1. Twój Plik Czysty", type=['xlsx'])
        with c2:
            f_report = st.file_uploader("2. Raport z systemu (z PR Value)", type=['xlsx'])
        
        if f_clean and f_report:
            if st.button("🔗 Połącz Pliki", type="primary"):
                try:
                    df_c = pd.read_excel(f_clean)
                    df_r = pd.read_excel(f_report)
                    df_final = merge_datasets(df_c, df_r)
                    st.success("Połączono pomyślnie!")
                    st.dataframe(df_final[['zrodlo', 'tytul', 'PR Value']].head(10), use_container_width=True)
                    b_fin = io.BytesIO()
                    with pd.ExcelWriter(b_fin, engine='xlsxwriter') as w:
                        df_final.to_excel(w, index=False)
                    st.download_button(
                        "⬇️ POBIERZ FINALNY RAPORT",
                        b_fin.getvalue(),
                        f"LGePR_FINAL_{datetime.now().strftime('%d%m')}.xlsx",
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        type="primary"
                    )
                except Exception as e:
                    st.error(f"Błąd łączenia: {e}")
        
        if st.button("← Wróć do Weryfikacji"):
            st.session_state.step = 3
            st.rerun()

if __name__ == "__main__":
    main()
