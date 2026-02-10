# LGePR Data Cleaner v7.0 (Cloud Edition)
# - Integracja ze Streamlit Secrets (trwała konfiguracja w chmurze).
# - Bramka hasła (Password Protection).
# - Ukrywanie UI (CSS Kill-Switch).
# - Pełna funkcjonalność AI i Edytora.

import streamlit as st
import pandas as pd
import re
import io
import time
import json
import os
import urllib.request
import urllib.error
import ssl
from datetime import datetime
import openpyxl

# --- 1. KONFIGURACJA STRONY ---
st.set_page_config(page_title="LGePR Cleaner", page_icon="🧹", layout="wide")

# --- 2. BRAMKA HASŁA (GATEKEEPER) ---
def check_password():
    """Zwraca True jeśli użytkownik wpisał poprawne hasło."""
    if "password_correct" not in st.session_state:
        st.session_state.password_correct = False

    if st.session_state.password_correct:
        return True

    # Formularz logowania
    st.markdown("### 🔒 Wymagane logowanie")
    pwd = st.text_input("Podaj hasło:", type="password")
    
    if st.button("Zaloguj"):
        # Sprawdzamy czy hasło jest w sekretach, jeśli nie - używamy domyślnego (dla testów lokalnych)
        secret_pwd = st.secrets.get("APP_PASSWORD", "admin123") 
        if pwd == secret_pwd:
            st.session_state.password_correct = True
            st.rerun()
        else:
            st.error("Nieprawidłowe hasło.")
    return False

if not check_password():
    st.stop() # ZATRZYMUJEMY APLIKACJĘ JEŚLI BRAK HASŁA

# --- 3. CSS KILL-SWITCH (UKRYWANIE ŚMIECI) ---
hide_ui_css = """
<style>
#MainMenu {visibility: hidden;}
footer {visibility: hidden;}
header {visibility: hidden;}
.stDeployButton {display:none;}
div[data-testid="stDecoration"] {display:none;}

/* Ukrywanie dokumentacji technicznej i błędów debugowania */
div[data-testid="stHelp"],
div[data-testid="stHelpDoc"],
table[data-testid="stHelpMembersTable"],
.st-emotion-cache-dr7npl,
.st-emotion-cache-11qqkrw,
.st-emotion-cache-znj1k1,
.st-emotion-cache-2fgyt4 p code,
div:has(> p > code:contains("None")) {
    display: none !important;
    visibility: hidden !important;
    height: 0px !important;
    opacity: 0 !important;
    pointer-events: none !important;
    font-size: 0px !important;
    margin: 0px !important;
    padding: 0px !important;
}
</style>
"""
st.markdown(hide_ui_css, unsafe_allow_html=True)

# ─────────────────────────────────────────────
# KONFIGURACJA STAŁA
# ─────────────────────────────────────────────
TITLE_MAX_LEN = 140
QUOTE_MAX_LEN = 450
ID_TITLE_CHARS = 20

FINAL_OUTPUT_ORDER = [
    'zrodlo', 'tytul', 'zasieg', 'data',
    'ENG Title', 'Division', 'Product', 'ESG', 'M/Z',
    'Links', 'Quote', 'LG', 'Exclusive', 'Photo',
    'clean_title', 'clean_quote', 'ID_MATCH', '_media_status'
]

SPECIAL_CHARS_PATTERN = re.compile(r'[.:!?"\'()\[\]/\\;,@]')
YEAR_PATTERN = re.compile(r'\b2026\b')

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
# LOGIKA SEKRETÓW (CLOUD)
# ─────────────────────────────────────────────
def get_cloud_config():
    """Pobiera konfigurację ze st.secrets lub zwraca puste wartości."""
    api_key = st.secrets.get("OPENAI_API_KEY", "")
    
    media_list = []
    if "MEDIA_LIST" in st.secrets:
        # Secrets zwraca listę automatycznie jeśli jest w TOML zdefiniowana jako tablica
        media_list = st.secrets["MEDIA_LIST"]
        # Jeśli z jakiegoś powodu jest stringiem (błąd formatowania), spróbuj rozdzielić
        if isinstance(media_list, str):
            media_list = [x.strip() for x in media_list.split(',')]
            
    return api_key, set(media_list)

# ─────────────────────────────────────────────
# AI WRAPPER (BATCHING)
# ─────────────────────────────────────────────
def call_openai_safe(system_prompt, user_content, api_key, model):
    url = "https://api.openai.com/v1/chat/completions"
    clean_key = api_key.strip()
    
    headers = {
        "Content-Type": "application/json", 
        "Authorization": f"Bearer {clean_key}"
    }
    
    payload_dict = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content}
        ],
        "max_tokens": 2500,
        "temperature": 0.1 
    }
    
    payload_bytes = json.dumps(payload_dict).encode("utf-8")

    for attempt in range(4):
        try:
            req = urllib.request.Request(url, data=payload_bytes, headers=headers, method="POST")
            ctx = ssl.create_default_context()
            with urllib.request.urlopen(req, timeout=60, context=ctx) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return data["choices"][0]["message"]["content"].strip()
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(2 * (attempt + 1))
                continue
            elif e.code == 401:
                return f"[API_ERROR: Invalid API Key (401)]"
            else:
                return f"[API_ERROR: HTTP {e.code}]"
        except Exception as e:
            return f"[API_ERROR: {str(e)[:100]}]"
    return "[API_ERROR: Connection Failed]"

FIX_SYSTEM_PROMPT = f"""
You are a Data Cleaning Bot. Correct data based on strict rules.

ALLOWED VALUES:
Division: {VALIDATION_RULES['Division']}
Photo: {VALIDATION_RULES['Photo']}
Exclusive: {VALIDATION_RULES['Exclusive']}
LG: {VALIDATION_RULES['LG']}
Products map: {json.dumps(PRODUCT_RULES)}

TASK:
- Input: JSON list of rows with errors.
- Output: JSON list of corrections.
- Format: [{{"index": 0, "changes": {{"Product": "Corrected", "LG": "Corrected"}}}}]
- Rules:
  1. Fix case sensitivity (oled -> OLED TV).
  2. Use context_division to pick correct Product.
  3. OUTPUT RAW JSON ONLY. No markdown.
"""

def fix_errors_with_ai_batched(df, api_key, model):
    error_rows = []
    
    for idx, row in df.iterrows():
        row_errors = {}
        div = str(row.get('Division', '')).strip()
        
        if not validate_val(div, VALIDATION_RULES["Division"]):
            if has_value(div): row_errors['Division'] = div
            
        allowed_prods = PRODUCT_RULES.get(div, [])
        prod_val = row.get('Product', '')
        if not validate_val(prod_val, allowed_prods):
            if has_value(prod_val): row_errors['Product'] = str(prod_val)
            
        for col in ["Photo", "Exclusive", "LG"]:
            val = row.get(col, '')
            if not validate_val(val, VALIDATION_RULES[col]):
                if has_value(val): row_errors[col] = str(val)

        if row_errors:
            error_rows.append({
                "index": idx,
                "tytul": str(row.get('tytul', '')),
                "current_values": row_errors,
                "context_division": div 
            })

    if not error_rows:
        return []

    BATCH_SIZE = 50
    all_corrections = []
    progress_bar = st.progress(0)
    total_batches = (len(error_rows) // BATCH_SIZE) + 1
    
    for i, offset in enumerate(range(0, len(error_rows), BATCH_SIZE)):
        batch = error_rows[offset : offset + BATCH_SIZE]
        user_content = json.dumps(batch, ensure_ascii=False)
        st.session_state.last_debug_input = user_content
        
        response = call_openai_safe(FIX_SYSTEM_PROMPT, user_content, api_key, model)
        
        if response.startswith("[API_ERROR"):
            st.error(f"Błąd w paczce {i+1}: {response}")
            continue

        try:
            match = re.search(r'\[.*\]', response, re.DOTALL)
            if match:
                batch_corrections = json.loads(match.group(0))
                all_corrections.extend(batch_corrections)
        except Exception:
            pass 
            
        progress_bar.progress(min((i + 1) / total_batches, 1.0))
        time.sleep(0.2)

    progress_bar.empty()
    return all_corrections

# ─────────────────────────────────────────────
# LOGIKA I POMOCNIKI
# ─────────────────────────────────────────────
def has_value(val):
    if val is None: return False
    try:
        if pd.isna(val): return False
    except: pass
    s = str(val).strip()
    if s == "": return False
    # Puste, szare pole w Streamlit to często None lub NaN, co jest odfiltrowane wyżej.
    # Tekst "None" lub "nan" przechodzi, bo to string.
    return True

def validate_val(val, allowed_list):
    if not has_value(val): return False
    v_str = str(val).strip()
    return v_str in [str(x) for x in allowed_list]

def highlight_errors(row):
    styles = ['' for _ in row.index]
    
    div_val = str(row.get('Division', '')).strip()
    div_idx = row.index.get_loc('Division') if 'Division' in row.index else -1
    
    if div_idx != -1 and not validate_val(div_val, VALIDATION_RULES["Division"]):
        styles[div_idx] = 'background-color: #ffcccc; color: darkred; font-weight: bold;'
    
    prod_idx = row.index.get_loc('Product') if 'Product' in row.index else -1
    if prod_idx != -1:
        allowed = PRODUCT_RULES.get(div_val, [])
        if not validate_val(row.get('Product', ''), allowed):
             styles[prod_idx] = 'background-color: #ffcccc; color: darkred; font-weight: bold;'

    for col in ["Photo", "Exclusive", "LG"]:
        idx = row.index.get_loc(col) if col in row.index else -1
        if idx != -1 and not validate_val(row.get(col, ''), VALIDATION_RULES[col]):
            styles[idx] = 'background-color: #ffcccc; color: darkred; font-weight: bold;'

    m_idx = row.index.get_loc('_media_status') if '_media_status' in row.index else -1
    if m_idx != -1 and row.get('_media_status') == 'BRAK':
        styles[m_idx] = 'background-color: #ffcccc; color: darkred; font-weight: bold;'

    return styles

def count_errors(df):
    err = 0
    for _, row in df.iterrows():
        fail = False
        div = str(row.get('Division', '')).strip()
        if not validate_val(div, VALIDATION_RULES["Division"]): fail = True
        
        allowed = PRODUCT_RULES.get(div, [])
        if not validate_val(row.get('Product', ''), allowed): fail = True
        
        for col in ["Photo", "Exclusive", "LG"]:
            if not validate_val(row.get(col, ''), VALIDATION_RULES[col]): fail = True
        if fail: err += 1
    return err

def normalize_domain(url):
    if pd.isna(url): return ""
    u = str(url).strip().lower()
    u = re.sub(r'^https?://', '', u)
    u = re.sub(r'^www\.', '', u)
    if u.endswith('/'): u = u[:-1]
    
    mapping = {
        'komputerswiat.pl': 'onet.pl',
        'benchmark.pl': 'wp.pl',
        'next.gazeta.pl': 'gazeta.pl',
        'cyfrowa.rp.pl': 'rp.pl'
    }
    if u in mapping: return mapping[u]
    if u.endswith('.onet.pl'): return 'onet.pl'
    if u.endswith('.wp.pl'): return 'wp.pl'
    return u

QUOTE_PROMPT = "Extract quote about LG/product. Output English. Max 125 chars. No special chars."
TITLE_PROMPT = "Translate title to American English. Shorten to 1 sentence. Max 120 chars. No special chars."

def scrape_article(url):
    try:
        from newspaper import Article
        if not str(url).startswith('http'): url = 'https://' + str(url)
        a = Article(url); a.download(); a.parse()
        return a.text[:4000] if a.text and len(a.text)>50 else ""
    except: return ""

def extract_specific_columns(f, sheet, media_list) -> pd.DataFrame:
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
        
        clean_src = normalize_domain(src_val)
        stat = "OK" if media_list and clean_src in media_list else "BRAK"
        if not media_list: stat = "N/A"
        
        lg_calc = "LG Electronics" if "LG" in str(tit_val).upper() else "N/A"
        
        day = str(dat_val)
        try: day = str(pd.to_datetime(dat_val).day)
        except: pass

        row = {
            'zrodlo': clean_src, 'tytul': tit_val, 'zasieg': rea_val,
            'data': day, '_orig_date': dat_val,
            'Links': re.sub(r'^https?://', '', str(link).strip()) if link else "",
            'Division': div_val, 'Product': prod_val, 'Exclusive': excl_val, 'Photo': phot_val,
            'ENG Title': "", 'Quote': "", 'ESG': "", 'M/Z': "",
            'LG': lg_calc, '_media_status': stat
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

def clean_text(t, l):
    if pd.isna(t): return ""
    x = str(t).strip()
    x = YEAR_PATTERN.sub("2026r", x)
    x = SPECIAL_CHARS_PATTERN.sub(" ", x)
    x = re.sub(r'\s+', ' ', x).strip()
    if len(x) > l: x = x[:l]; x = x[:x.rfind(' ')]
    return x.strip()

def recalculate_after_edit(df, media_list):
    if media_list:
        df['_media_status'] = df['zrodlo'].apply(
            lambda x: "OK" if normalize_domain(x) in media_list else "BRAK"
        )
    return df

# ─────────────────────────────────────────────
# MAIN APP
# ─────────────────────────────────────────────
def main():
    st.title("🧹 LGePR Data Cleaner v7.0")

    # Inicjalizacja konfiguracji (CLOUD)
    if 'config_loaded' not in st.session_state:
        secret_key, secret_media = get_cloud_config()
        st.session_state.saved_api_key = secret_key
        st.session_state.media_list = secret_media
        st.session_state.config_loaded = True
        st.session_state.step = 1
        st.session_state.df_work = None
        st.session_state.ai_proposals = None

    with st.sidebar:
        st.header("Ustawienia")
        
        # KEY
        if st.session_state.saved_api_key:
            st.success("✅ Klucz API załadowany z Secrets")
            active_key = st.session_state.saved_api_key
        else:
            active_key = st.text_input("OpenAI API Key (Tymczasowy)", type="password")
            if active_key and not active_key.startswith("sk-"): st.warning("⚠️ Zły format klucza")
            
        model = st.selectbox("Model", ["gpt-4.1-mini", "gpt-4o-mini"])
        
        st.divider()
        st.header("Media")
        
        # MEDIA LIST
        current_list_txt = "\n".join(sorted(st.session_state.media_list))
        if st.session_state.saved_api_key and st.session_state.media_list:
             st.success(f"✅ Załadowano {len(st.session_state.media_list)} mediów z Secrets")
             with st.expander("Podgląd listy"):
                 st.text_area("Lista:", current_list_txt, height=150, disabled=True)
        else:
            txt_m = st.text_area("Lista Mediów (Tymczasowa):", current_list_txt, height=150)
            if st.button("Użyj tej listy"):
                cl = [normalize_domain(x) for x in txt_m.split('\n') if x.strip()]
                st.session_state.media_list = set(cl)
                st.success("Lista tymczasowa aktywna")

        st.info("ℹ️ Aby zapisać ustawienia na stałe, dodaj je do 'Secrets' w panelu Streamlit Cloud.")

    # STEPS
    s1, s2, s3 = st.columns(3)
    curr = st.session_state.step
    s1.info("1. Upload") if curr==1 else s1.write("1. Upload")
    s2.info("2. Weryfikacja") if curr==2 else s2.write("2. Weryfikacja")
    s3.info("3. Download") if curr==3 else s3.write("3. Download")
    st.divider()

    # KROK 1
    if curr == 1:
        f = st.file_uploader("Wgraj raport (.xlsx)", type=['xlsx', 'xlsm'])
        if f:
            try:
                f.seek(0)
                wb = openpyxl.load_workbook(f, read_only=True)
                sheets = wb.sheetnames; wb.close()
                sh = st.selectbox("Arkusz:", sheets)
                list_ok = len(st.session_state.media_list) > 0
                if not list_ok: st.warning("⚠️ Brak listy mediów! Wklej ją w panelu bocznym lub dodaj do Secrets.")
                if st.button("🚀 Wczytaj", type="primary", disabled=not list_ok):
                    f.seek(0)
                    df = extract_specific_columns(f, sh, st.session_state.media_list)
                    st.session_state.df_work = df
                    st.session_state.step = 2
                    st.rerun()
            except Exception as e: st.error(f"Błąd: {e}")

    # KROK 2
    elif curr == 2:
        df = st.session_state.df_work
        errs = count_errors(df)
        miss = df[df['_media_status']=='BRAK'].shape[0]

        if errs > 0: st.error(f"🚨 Błędy walidacji: {errs} wierszy.")
        if miss > 0: st.error(f"🚨 Braki mediów: {miss} wierszy.")
        if errs==0 and miss==0: st.success("✅ Dane poprawne")

        cols = df.columns.tolist()
        if '_media_status' in cols: cols.insert(0, cols.pop(cols.index('_media_status')))
        
        st.markdown("### 🔍 1. Podgląd błędów (Tylko do odczytu)")
        st.caption("🔴 Czerwone pola wymagają poprawy. AI naprawia literówki, ale puste pola musisz wypełnić ręcznie poniżej.")
        st.dataframe(df[cols].style.apply(highlight_errors, axis=1), use_container_width=True, height=300)

        st.markdown("### ✏️ 2. Edytor Danych (Tu poprawiasz)")
        edited_df = st.data_editor(df[cols], use_container_width=True, num_rows="dynamic", height=300, key="editor")
        
        edited_df = recalculate_after_edit(edited_df, st.session_state.media_list)
        if not df.equals(edited_df):
            st.session_state.df_work = edited_df
            st.rerun()
        df = st.session_state.df_work

        if errs > 0:
            st.divider()
            
            if st.button("🤖 Napraw WSZYSTKIE błędy z AI (Batch)", type="primary", disabled=not active_key):
                with st.spinner("AI analizuje i naprawia wszystkie błędy (może to chwilę potrwać)..."):
                    props = fix_errors_with_ai_batched(df, active_key, model)
                    if props: 
                        st.session_state.ai_proposals = props
                        st.success(f"Znaleziono {len(props)} sugerowanych poprawek!")
                    else: 
                        st.warning("AI nie znalazło błędów do poprawy (lub same puste pola).")
            
            # EDYTOWALNA TABELA PROPOZYCJI
            if st.session_state.ai_proposals:
                st.write("---")
                st.markdown("#### 📝 Zweryfikuj i zatwierdź zmiany AI:")
                st.caption("Możesz ręcznie edytować kolumnę 'Propozycja (AI)' przed zatwierdzeniem.")
                
                proposal_list = []
                for p in st.session_state.ai_proposals:
                    for k, v in p['changes'].items():
                        proposal_list.append({
                            "Wiersz_Idx": p['index'],
                            "Wiersz": p['index'] + 2,
                            "Kolumna": k,
                            "Było (Błąd)": df.at[p['index'], k],
                            "Propozycja (AI)": v
                        })
                
                if proposal_list:
                    prop_df = pd.DataFrame(proposal_list)
                    edited_props = st.data_editor(
                        prop_df,
                        use_container_width=True,
                        disabled=["Wiersz", "Kolumna", "Było (Błąd)"],
                        column_config={"Wiersz_Idx": None},
                        hide_index=True,
                        key="props_editor"
                    )
                    
                    c1, c2 = st.columns([1,4])
                    if c1.button("✅ Zatwierdź wszystkie zmiany"):
                        for _, row_prop in edited_props.iterrows():
                            idx = row_prop['Wiersz_Idx']
                            col = row_prop['Kolumna']
                            val = row_prop['Propozycja (AI)']
                            st.session_state.df_work.at[idx, col] = val
                        st.session_state.ai_proposals = None
                        st.success("Wszystkie poprawki naniesione!")
                        st.rerun()
                    if c2.button("❌ Anuluj"):
                        st.session_state.ai_proposals = None; st.rerun()

        st.divider()
        st.markdown("### Tłumaczenie / Cytaty")
        c1, c2 = st.columns([1, 2])
        with c1:
            lim = st.number_input("Limit", 0, len(df), 0)
            if st.button("🤖 Uruchom", disabled=not active_key):
                cnt = len(df) if lim == 0 else lim
                pb = st.progress(0)
                for i in range(cnt):
                    if not df.at[i, 'ENG Title'] and df.at[i, 'tytul']:
                        df.at[i, 'ENG Title'] = call_openai_safe(TITLE_PROMPT, str(df.at[i, 'tytul']), active_key, model)
                    lnk = df.at[i, 'Links']
                    if not df.at[i, 'Quote'] and lnk:
                        txt = scrape_article("https://"+lnk)
                        if txt: df.at[i, 'Quote'] = call_openai_safe(QUOTE_PROMPT, txt, active_key, model)
                        else: df.at[i, 'Quote'] = "[FAIL]"
                    time.sleep(0.5); pb.progress((i+1)/cnt)
                st.session_state.df_work = df; st.rerun()
        with c2:
            st.markdown("<br>", unsafe_allow_html=True)
            if st.button("Dalej →"): st.session_state.step = 3; st.rerun()

    # KROK 3
    elif curr == 3:
        df = st.session_state.df_work.copy()
        df['clean_title'] = df['tytul'].apply(lambda x: clean_text(x, TITLE_MAX_LEN))
        df['clean_quote'] = df['Quote'].apply(lambda x: clean_text(x, QUOTE_MAX_LEN))
        df['ID_MATCH'] = df.apply(generate_id_match, axis=1)
        if st.session_state.media_list:
            df['_media_status'] = df['zrodlo'].apply(lambda x: "OK" if str(x) in st.session_state.media_list else "BRAK")

        for c in FINAL_OUTPUT_ORDER:
            if c not in df.columns: df[c] = ""
        df_fin = df[FINAL_OUTPUT_ORDER]
        
        st.markdown("### Podgląd końcowy (Edytowalny)")
        edited_fin = st.data_editor(df_fin.style.apply(highlight_errors, axis=1), use_container_width=True, num_rows="dynamic")
        df_fin = edited_fin
        
        b = io.BytesIO()
        with pd.ExcelWriter(b, engine='xlsxwriter') as w:
            df_fin.to_excel(w, sheet_name='Dane_dla_Bota', index=False)
            miss = df_fin[df_fin['_media_status']=="BRAK"]
            if not miss.empty: miss.to_excel(w, sheet_name='Brakujace', index=False)
            
        st.download_button("⬇️ XLSX", b.getvalue(), f"LGePR_{datetime.now().strftime('%H%M')}.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", type="primary")
        if st.button("Reset"): st.session_state.clear(); st.rerun()

if __name__ == "__main__":
    main()
