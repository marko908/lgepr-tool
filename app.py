# LGePR Data Cleaner v8.1
# 1. FIX: Naprawiono NameError (stałe przeniesione do globalnego zasięgu).
# 2. FEATURE: Dodano podgląd tabeli zaraz po wgraniu pliku (Krok 1).
# 3. CSS: Zachowano agresywne ukrywanie dokumentacji.

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
from newspaper import Article

# ─────────────────────────────────────────────
# 1. KONFIGURACJA STRONY I CSS
# ─────────────────────────────────────────────
st.set_page_config(page_title="LGePR Cleaner", page_icon="🧹", layout="wide")

# CSS KILL-SWITCH (Ukrywanie śmieci)
hide_ui_css = """
<style>
#MainMenu {visibility: hidden;}
footer {visibility: hidden;}
header {visibility: hidden;}
.stDeployButton {display:none;}
div[data-testid="stDecoration"] {display:none;}

/* Ukrywanie dokumentacji technicznej */
div[data-testid="stHelp"],
div[data-testid="stHelpDoc"],
table[data-testid="stHelpMembersTable"],
.st-emotion-cache-dr7npl,
.st-emotion-cache-11qqkrw,
.st-emotion-cache-znj1k1,
.st-emotion-cache-1r6slb0, 
div:has(> span:contains("DeltaGenerator")),
p:contains("None"), 
code:contains("None") {
    display: none !important;
    visibility: hidden !important;
    height: 0px !important;
    opacity: 0 !important;
    pointer-events: none !important;
    position: absolute !important;
    top: -9999px !important;
}
</style>
"""
st.markdown(hide_ui_css, unsafe_allow_html=True)

# ─────────────────────────────────────────────
# 2. STAŁE GLOBALNE (DEFINIOWANE NA GÓRZE, ABY UNIKNĄĆ NAME ERROR)
# ─────────────────────────────────────────────
TITLE_MAX_LEN = 140
QUOTE_MAX_LEN = 450
ID_TITLE_CHARS = 20

# Regexy kompilujemy raz na początku
SPECIAL_CHARS_PATTERN = re.compile(r'[.:!?"\'()\[\]/\\;,@]')
YEAR_PATTERN = re.compile(r'\b2026\b')

FINAL_OUTPUT_ORDER = [
    'zrodlo', 'tytul', 'zasieg', 'data',
    'ENG Title', 'Division', 'Product', 'ESG', 'M/Z',
    'Links', 'Quote', 'LG', 'Exclusive', 'Photo',
    'clean_title', 'clean_quote', 'ID_MATCH', '_media_status'
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
# 3. BRAMKA LOGOWANIA
# ─────────────────────────────────────────────
def check_password():
    if "password_correct" not in st.session_state:
        st.session_state.password_correct = False
    if st.session_state.password_correct:
        return True
    
    st.markdown("### 🔒 Dostęp autoryzowany")
    pwd = st.text_input("Hasło:", type="password")
    if st.button("Zaloguj"):
        secret_pwd = st.secrets.get("APP_PASSWORD", "admin123")
        if pwd == secret_pwd:
            st.session_state.password_correct = True
            st.rerun()
        else:
            st.error("Błędne hasło")
    return False

if not check_password():
    st.stop()

# ─────────────────────────────────────────────
# 4. POMOCNIKI (FUNKCJE LOGICZNE)
# ─────────────────────────────────────────────
def get_cloud_config():
    api_key = st.secrets.get("OPENAI_API_KEY", "")
    media_list = st.secrets.get("MEDIA_LIST", [])
    if isinstance(media_list, str): 
        media_list = [x.strip() for x in media_list.split(',')]
    return api_key, set(media_list)

def has_value(val):
    if val is None: return False
    try:
        if pd.isna(val): return False
    except: pass
    s = str(val).strip()
    if s == "" or s.lower() == "nan": return False
    return True

def validate_val(val, allowed_list):
    if not has_value(val): return False
    return str(val).strip() in [str(x) for x in allowed_list]

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

def normalize_domain(url):
    if pd.isna(url): return ""
    u = str(url).strip().lower()
    u = re.sub(r'^https?://', '', u)
    u = re.sub(r'^www\.', '', u)
    if u.endswith('/'): u = u[:-1]
    mapping = {'komputerswiat.pl': 'onet.pl', 'benchmark.pl': 'wp.pl'}
    return mapping.get(u, u)

def scrape_article_data(url):
    try:
        if not str(url).startswith('http'): url = 'https://' + str(url)
        a = Article(url)
        a.download()
        a.parse()
        return {
            "text": a.text[:4000] if a.text else "",
            "image_url": a.top_image if a.top_image else None
        }
    except:
        return {"text": "", "image_url": None}

def merge_datasets(clean_df, report_df):
    def create_key(row, media_col, title_col, date_col):
        m = normalize_domain(str(row[media_col]))
        t = str(row[title_col]).strip().lower()[:30]
        d = str(row[date_col]).strip()[:10]
        return f"{m}|{t}|{d}"

    clean_df['__merge_key'] = clean_df.apply(lambda r: create_key(r, 'zrodlo', 'tytul', '_orig_date'), axis=1)
    report_df['__merge_key'] = report_df.apply(lambda r: create_key(r, 'Media', 'Tytuł', 'Published'), axis=1)
    
    pr_map = dict(zip(report_df['__merge_key'], report_df['PR Value']))
    clean_df['PR Value'] = clean_df['__merge_key'].map(pr_map)
    clean_df.drop(columns=['__merge_key'], inplace=True)
    return clean_df

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
            'ENG Title': "", 'Quote': "", 'ESG': "", 'M/Z': "", 'LG': lg_calc, '_media_status': stat
        }
        data.append(row)
    wb.close()
    return pd.DataFrame(data)

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

def generate_id_match(row):
    src = str(row.get('zrodlo', '')).strip()
    # Tutaj używamy stałej globalnej ID_TITLE_CHARS
    tit = str(row.get('clean_title', '') or row.get('tytul', ''))[:ID_TITLE_CHARS].strip()
    try: d = pd.to_datetime(row.get('_orig_date')).strftime("%Y%m%d")
    except: d = str(row.get('_orig_date', ''))[:8].replace('-','')
    return f"{src}|{tit}|{d}"

def clean_text(t, l):
    if pd.isna(t): return ""
    x = str(t).strip()
    # Tutaj używamy stałych globalnych regex
    x = YEAR_PATTERN.sub("2026r", x)
    x = SPECIAL_CHARS_PATTERN.sub(" ", x)
    x = re.sub(r'\s+', ' ', x).strip()
    if len(x) > l: x = x[:l]; x = x[:x.rfind(' ')]
    return x.strip()

# ─────────────────────────────────────────────
# 5. AI ENGINE (TEKST + VISION)
# ─────────────────────────────────────────────
def call_openai_single(prompt, key, model):
    req_data = {
        "model": model,
        "messages": [{"role": "system", "content": "You are a Data Analyst."}, {"role": "user", "content": prompt}],
        "temperature": 0.1
    }
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {key}"}
    req = urllib.request.Request("https://api.openai.com/v1/chat/completions", json.dumps(req_data).encode(), headers)
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())['choices'][0]['message']['content']

def call_openai_vision(prompt, img_url, key):
    req_data = {
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
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {key}"}
    req = urllib.request.Request("https://api.openai.com/v1/chat/completions", json.dumps(req_data).encode(), headers)
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())['choices'][0]['message']['content']

def analyze_row_with_ai(row, api_key, model):
    needs_div = not has_value(row['Division'])
    needs_prod = not has_value(row['Product'])
    needs_excl = not has_value(row['Exclusive'])
    needs_quote = not has_value(row['Quote'])
    needs_photo = not has_value(row['Photo'])
    
    if not any([needs_div, needs_prod, needs_excl, needs_quote, needs_photo]):
        return None

    url = row.get('Links', '')
    if not url: return None
    scraped = scrape_article_data(url)
    text_content = scraped['text']
    img_url = scraped['image_url']
    
    if not text_content: return None

    updates = {}

    if any([needs_div, needs_prod, needs_excl, needs_quote]):
        current_div = row.get('Division', '')
        constraint = ""
        if has_value(current_div):
            constraint = f"CONSTRAINT: Division is locked to '{current_div}'. Pick Product ONLY from this division list."

        prompt = f"""
        Analyze article text. Rules:
        1. Context: LG Electronics only.
        2. Division/Product: Assign based on map: {json.dumps(PRODUCT_RULES)}.
        3. {constraint}
        4. Exclusive rules: <33% -> '33', 40-47% -> '50', >60% -> '66', 100% -> 'Exclusive'.
        5. Quote: Extract 1 relevant sentence (max 150 chars).
        
        Return JSON: {{ "Division": "...", "Product": "...", "Exclusive": "...", "Quote": "..." }}
        Text: {text_content[:2000]}
        """
        
        try:
            resp = call_openai_single(prompt, api_key, "gpt-4o-mini")
            data = json.loads(resp)
            if needs_div: updates['Division'] = data.get('Division', '')
            if needs_prod: updates['Product'] = data.get('Product', '')
            if needs_excl: updates['Exclusive'] = data.get('Exclusive', '')
            if needs_quote: updates['Quote'] = data.get('Quote', '')
        except: pass

    if needs_photo and img_url:
        try:
            vision_prompt = "What is in this image related to LG? Return ONLY one string: 'LGE logo', 'product', 'personnel', or 'None'."
            resp_vision = call_openai_vision(vision_prompt, img_url, api_key)
            clean_resp = resp_vision.replace("'", "").replace('"', '').strip()
            if clean_resp in ["LGE logo", "product", "personnel", "None"]:
                updates['Photo'] = clean_resp
            else:
                updates['Photo'] = "None"
        except:
            updates['Photo'] = "[IMG_ERR]"
    elif needs_photo and not img_url:
        updates['Photo'] = "None"

    if not updates: return None
    return {"index": row.name, "changes": updates}

# ─────────────────────────────────────────────
# 6. GŁÓWNA APLIKACJA
# ─────────────────────────────────────────────
def main():
    st.title("🧹 LGePR Data Cleaner v8.1")

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
        if st.session_state.saved_api_key:
            st.success("✅ Klucz API aktywny")
            active_key = st.session_state.saved_api_key
        else:
            active_key = st.text_input("Klucz API (Tymczasowy)", type="password")
        
        st.divider()
        st.header("Media")
        if st.session_state.media_list:
             st.success(f"✅ Baza mediów: {len(st.session_state.media_list)}")
        else:
            st.warning("⚠️ Brak listy mediów w Secrets")

    s1, s2, s3, s4 = st.columns(4)
    curr = st.session_state.step
    s1.info("1. Upload") if curr==1 else s1.write("1. Upload")
    s2.info("2. Analiza AI") if curr==2 else s2.write("2. Analiza AI")
    s3.info("3. Weryfikacja") if curr==3 else s3.write("3. Weryfikacja")
    s4.info("4. Merge") if curr==4 else s4.write("4. Merge")
    st.divider()

    # KROK 1: UPLOAD & PREVIEW
    if curr == 1:
        f = st.file_uploader("Wgraj plik roboczy (.xlsx)", type=['xlsx', 'xlsm'])
        if f:
            try:
                # Wstępny odczyt arkuszy
                wb = openpyxl.load_workbook(f, read_only=True)
                sheets = wb.sheetnames; wb.close()
                sh = st.selectbox("Wybierz arkusz:", sheets)
                
                # Przycisk wczytania
                if st.button("🚀 Załaduj i Pokaż", type="primary"):
                    f.seek(0)
                    df = extract_specific_columns(f, sh, st.session_state.media_list)
                    st.session_state.df_work = df
                    st.success(f"Wczytano {len(df)} wierszy.")
                    st.rerun() # Odśwież, żeby pokazać tabelę poniżej
            except Exception as e: st.error(f"Błąd pliku: {e}")
        
        # PODGLĄD PLIKU (Jeśli już wczytany)
        if st.session_state.df_work is not None:
            st.markdown("### 📄 Podgląd danych (Surowe)")
            st.dataframe(st.session_state.df_work.head(10), use_container_width=True)
            
            # Przycisk przejścia dalej
            col_btn, _ = st.columns([1,4])
            if col_btn.button("Przejdź do Analizy →", type="primary"):
                st.session_state.step = 2
                st.rerun()

    # KROK 2: ANALIZA AI
    elif curr == 2:
        df = st.session_state.df_work
        st.markdown("### 🧠 Analiza treści i obrazu")
        st.info("AI przeanalizuje linki, pobierze treść, zdjęcia i uzupełni braki. To może chwilę potrwać.")
        
        c1, c2 = st.columns([1,3])
        if c1.button("▶️ Uruchom Pełną Analizę", type="primary", disabled=not active_key):
            progress_bar = st.progress(0)
            status_text = st.empty()
            proposals = []
            total = len(df)
            
            for i, row in df.iterrows():
                status_text.text(f"Analizuję wiersz {i+1}/{total}: {str(row['tytul'])[:30]}...")
                update = analyze_row_with_ai(row, active_key, "gpt-4o-mini")
                if update: proposals.append(update)
                progress_bar.progress((i + 1) / total)
            
            status_text.success("Analiza zakończona!")
            if proposals:
                st.session_state.ai_proposals = proposals
                st.rerun()
            else:
                st.warning("Wszystko wygląda na uzupełnione.")

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

    # KROK 3: WERYFIKACJA
    elif curr == 3:
        df = st.session_state.df_work
        
        # Tutaj używamy TITLE_MAX_LEN, które teraz jest globalne i widoczne!
        df['clean_title'] = df['tytul'].apply(lambda x: clean_text(x, TITLE_MAX_LEN))
        df['clean_quote'] = df['Quote'].apply(lambda x: clean_text(x, QUOTE_MAX_LEN))
        df['ID_MATCH'] = df.apply(generate_id_match, axis=1)
        
        errs = count_errors(df)
        if errs > 0: st.error(f"Pozostało błędów: {errs}")
        else: st.success("Dane czyste!")

        cols = df.columns.tolist()
        if '_media_status' in cols: cols.insert(0, cols.pop(cols.index('_media_status')))
        
        st.markdown("### ✏️ Finalna Weryfikacja")
        edited_fin = st.data_editor(
            df[cols].style.apply(highlight_errors, axis=1),
            use_container_width=True,
            num_rows="dynamic",
            height=500
        )
        
        if not df.equals(edited_fin):
            st.session_state.df_work = edited_fin
            st.rerun()

        col_d1, col_d2 = st.columns(2)
        with col_d1:
            b = io.BytesIO()
            with pd.ExcelWriter(b, engine='xlsxwriter') as w:
                edited_fin.to_excel(w, sheet_name='Dane_Clean', index=False)
            st.download_button("⬇️ Pobierz Czysty Plik", b.getvalue(), "LGePR_Clean.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", type="primary")
        
        with col_d2:
            st.markdown("Masz już plik z PR Value?")
            if st.button("Przejdź do Mergowania →"):
                st.session_state.step = 4
                st.rerun()

    # KROK 4: MERGE
    elif curr == 4:
        st.markdown("### 🔗 Łączenie z Raportem PR Value")
        st.info("Wgraj plik z Kroku 3 oraz raport z systemu.")
        
        c1, c2 = st.columns(2)
        f_clean = c1.file_uploader("1. Plik Czysty", type=['xlsx'])
        f_report = c2.file_uploader("2. Raport z systemu", type=['xlsx'])
        
        if f_clean and f_report:
            if st.button("🔗 Połącz Pliki", type="primary"):
                try:
                    df_c = pd.read_excel(f_clean)
                    df_r = pd.read_excel(f_report)
                    df_final = merge_datasets(df_c, df_r)
                    
                    st.success("Połączono!")
                    st.dataframe(df_final[['zrodlo', 'tytul', 'PR Value']].head())
                    
                    b_fin = io.BytesIO()
                    with pd.ExcelWriter(b_fin, engine='xlsxwriter') as w:
                        df_final.to_excel(w, index=False)
                    st.download_button("⬇️ POBIERZ FINALNY RAPORT", b_fin.getvalue(), f"LGePR_FINAL_{datetime.now().strftime('%d%m')}.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", type="primary")
                except Exception as e: st.error(f"Błąd: {e}")
        
        if st.button("← Wróć"):
            st.session_state.step = 3
            st.rerun()

if __name__ == "__main__":
    main()
