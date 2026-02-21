# import json
# import requests
# import pyodbc
# import os
# import xml.etree.ElementTree as ET
# import logging
# from azure.storage.blob import BlobServiceClient

# # Configure logging
# logging.basicConfig(
#     level=logging.INFO,
#     format="%(asctime)s [%(levelname)s] %(message)s",
#     handlers=[logging.StreamHandler()]
# )
# logger = logging.getLogger(__name__)

# # Language Code Mapping (user-friendly to model-friendly)
# lang_map = {
#     "ar-SA": "ar_AR",
#     "de-DE": "de_DE",  # Ensure to map 'de-DE' to 'de_DE'
#     "en-US": "en_XX",
#     "es-ES": "es_XX",  # Map 'es-ES' to 'es_XX'
#     "fr-FR": "fr_XX",
#     "hi-IN": "hi_IN",
#     "it-IT": "it_IT",
#     "ja-JP": "ja_XX",
#     "ko-KR": "ko_KR",
#     "nl-NL": "nl_XX",
#     "pl-PL": "pl_PL",
#     "pt-PT": "pt_XX",
#     "pt-BR": "pt_BR",
#     "ru-RU": "ru_RU",
#     "sv-SE": "sv_SE",
#     "tr-TR": "tr_TR",
#     "uk-UA": "uk_UA",
#     "zh-CN": "zh_CN",
#     "zh-TW": "zh_TW",
# }

# def load_config(config_path):
#     logger.info(f"🔄 Loading config from {config_path}")
#     try:
#         with open(config_path, "r", encoding="utf-8") as f:
#             config = json.load(f)
#         logger.info("✅ Config loaded successfully")
#         return config
#     except Exception as e:
#         logger.error(f"❌ Error loading config: {e}")
#         raise

# def connect_sql(sql_conn_str):
#     logger.info("🔄 Connecting to SQL database...")
#     try:
#         conn = pyodbc.connect(sql_conn_str)
#         logger.info("✅ SQL connection established")
#         return conn
#     except Exception as e:
#         logger.error(f"❌ Error connecting to SQL database: {e}")
#         raise

# def query_translation(cursor, source_text, target_lang):
#     logger.debug(f"🔍 Querying SQL for translation: SourceText = {source_text}, TargetLang = {target_lang}")
#     try:
#         cursor.execute("SELECT TranslatedText FROM Translations WHERE SourceText=? AND TargetLang=?", (source_text, target_lang))
#         row = cursor.fetchone()
#         if row:
#             logger.info(f"✅ Found cached translation for: {source_text}")
#             return row[0]
#         else:
#             logger.info(f"⚠️ No cached translation for: {source_text}")
#             return None
#     except Exception as e:
#         logger.error(f"❌ Error querying translation: {e}")
#         raise

# def insert_translation(cursor, source_text, target_lang, translated_text):
#     logger.debug(f"🔄 Inserting translation into SQL: SourceText = {source_text}, TargetLang = {target_lang}")
#     try:
#         cursor.execute("INSERT INTO Translations (SourceText, TargetLang, TranslatedText) VALUES (?, ?, ?)",
#                        (source_text, target_lang, translated_text))
#         logger.info(f"✅ Inserted translation for: {source_text}")
#     except Exception as e:
#         logger.error(f"❌ Error inserting translation: {e}")
#         raise

# def translate_with_vm(text, target_lang, vm_ip = "20.244.31.97", port=8000):
#     logger.info(f"🔄 Translating text using VM-hosted API: {text[:30]}... → {target_lang}")
    
#     # Map the target language to the tokenizer's expected format
#     if target_lang not in lang_map:
#         logger.error(f"❌ Unsupported target language code: {target_lang}")
#         return None
    
#     mapped_lang = lang_map[target_lang]  # e.g., 'de-DE' becomes 'de_DE'

#     api_url = f"http://{vm_ip}:{port}/translate"
    
#     # Prepare the data payload
#     data = {
#         "text": [text],
#         "src_lang": "en_XX",  # Assuming the source language is always English (en_XX)
#         "tgt_lang": mapped_lang  # Use the mapped language format
#     }

#     # Make the request to the VM-hosted FastAPI server
#     try:
#         response = requests.post(api_url, json=data)
#         if response.status_code == 200:
#             result = response.json()
#             translated_text = result.get("translated_texts", [None])[0]
#             logger.info(f"✅ Translation successful: {translated_text[:30]}...")  # Log first 30 chars for brevity
#             return translated_text
#         else:
#             logger.error(f"❌ VM API error {response.status_code}: {response.text}")
#             return None
#     except Exception as e:
#         logger.error(f"❌ Error while calling VM API: {e}")
#         return None

# def process_xlf_file(xlf_path, target_lang, conn, hf_token, vm_ip):
#     logger.info(f"🔄 Processing {xlf_path} for {target_lang}")
#     tree = ET.parse(xlf_path)
#     root = tree.getroot()

#     cursor = conn.cursor()
#     translated_count = 0
#     skipped_count = 0

#     for tu in root.findall(".//trans-unit"):
#         source_elem = tu.find("source")
#         target_elem = tu.find("target")
#         if source_elem is None or target_elem is None:
#             continue
#         source_text = source_elem.text or ""
#         if not source_text.strip():
#             continue

#         # 1. Check SQL cache
#         cached = query_translation(cursor, source_text, target_lang)
#         if cached:
#             target_elem.text = cached
#             skipped_count += 1
#             logger.info(f"⚠️ Using cached translation for: {source_text}")
#             continue

#         # 2. Call VM-hosted API
#         translated = translate_with_vm(source_text, target_lang, vm_ip)
#         if translated:
#             insert_translation(cursor, source_text, target_lang, translated)
#             target_elem.text = translated
#             translated_count += 1
#         else:
#             skipped_count += 1

#     conn.commit()
#     tree.write(xlf_path, encoding="utf-8", xml_declaration=True)
#     logger.info(f"✅ Completed {xlf_path}: {translated_count} new, {skipped_count} cached/skipped")

# def main(config_path):
#     logger.info("🔄 Starting translation process...")
#     config = load_config(config_path)

#     storage_conn_str = config["blob_connection_string"]
#     raw_container = config["raw_container"]
#     translated_container = config["translated_container"]
#     sql_conn_str = config["sql_conn_str"]
#     hf_token = config["hf_api_token"]
#     vm_ip = config["vm_ip"]
#     vm_port = config.get("vm_port", 8000)

#     blob_service = BlobServiceClient.from_connection_string(storage_conn_str)
#     raw_client = blob_service.get_container_client(raw_container)
#     trans_client = blob_service.get_container_client(translated_container)
#     if not trans_client.exists():
#         trans_client.create_container()

#     conn = connect_sql(sql_conn_str)

#     for blob in raw_client.list_blobs():
#         blob_name = blob.name
#         local_path = os.path.basename(blob_name)
#         with open(local_path, "wb") as f:
#             f.write(raw_client.get_blob_client(blob).download_blob().readall())

#         parts = blob_name.split(".")
#         if len(parts) < 2:
#             logger.warning(f"⚠️ Skipping blob {blob_name}, unexpected name format")
#             continue
#         target_lang = parts[-2]  # e.g. MyFile.resx.fr-FR.xlf → "fr-FR"

#         logger.info(f"🌍 Translating {blob_name} → {target_lang}")
#         process_xlf_file(local_path, target_lang, conn, hf_token, vm_ip)

#         # Upload translated
#         trans_client.upload_blob(name=blob_name, data=open(local_path, "rb"), overwrite=True)
#         raw_client.delete_blob(blob_name)
#         os.remove(local_path)
#         logger.info(f"📤 Uploaded translated {blob_name} and removed from raw container")

#     conn.close()
#     logger.info("🎉 Phase2 translation completed successfully")

# if __name__ == "__main__":
#     import sys
#     if len(sys.argv) != 2:
#         logger.error("❌ Usage: python translation.py <config.json>")
#         sys.exit(1)
#     main(sys.argv[1])



import json
import requests
import pyodbc
import os
import xml.etree.ElementTree as ET
import logging
from azure.storage.blob import BlobServiceClient

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# Language Code Mapping (user-friendly to model-friendly)
lang_map = {
    "ar-SA": "ar_AR",
    "de-DE": "de_DE",  # Ensure to map 'de-DE' to 'de_DE'
    "en-US": "en_XX",
    "es-ES": "es_XX",  # Map 'es-ES' to 'es_XX'
    "fr-FR": "fr_XX",
    "hi-IN": "hi_IN",
    "it-IT": "it_IT",
    "ja-JP": "ja_XX",
    "ko-KR": "ko_KR",
    "nl-NL": "nl_XX",
    "pl-PL": "pl_PL",
    "pt-PT": "pt_XX",
    "pt-BR": "pt_BR",
    "ru-RU": "ru_RU",
    "sv-SE": "sv_SE",
    "tr-TR": "tr_TR",
    "uk-UA": "uk_UA",
    "zh-CN": "zh_CN",
    "zh-TW": "zh_TW",
}

def load_config(config_path):
    logger.info(f"🔄 Loading config from {config_path}")
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)
        logger.info("✅ Config loaded successfully")
        return config
    except Exception as e:
        logger.error(f"❌ Error loading config: {e}")
        raise

def connect_sql(sql_conn_str):
    logger.info("🔄 Connecting to SQL database...")
    try:
        conn = pyodbc.connect(sql_conn_str)
        logger.info("✅ SQL connection established")
        return conn
    except Exception as e:
        logger.error(f"❌ Error connecting to SQL database: {e}")
        raise

def query_translation(cursor, source_text, target_lang):
    logger.debug(f"🔍 Querying SQL for translation: SourceText = {source_text}, TargetLang = {target_lang}")
    try:
        cursor.execute("SELECT TranslatedText FROM Translations WHERE SourceText=? AND TargetLang=?", (source_text, target_lang))
        row = cursor.fetchone()
        if row:
            logger.info(f"✅ Found cached translation for: {source_text}")
            return row[0]
        else:
            logger.info(f"⚠️ No cached translation for: {source_text}")
            return None
    except Exception as e:
        logger.error(f"❌ Error querying translation: {e}")
        raise

def insert_translation(cursor, source_text, target_lang, translated_text):
    logger.debug(f"🔄 Inserting translation into SQL: SourceText = {source_text}, TargetLang = {target_lang}")
    try:
        cursor.execute("INSERT INTO Translations (SourceText, TargetLang, TranslatedText) VALUES (?, ?, ?)",
                       (source_text, target_lang, translated_text))
        logger.info(f"✅ Inserted translation for: {source_text}")
    except Exception as e:
        logger.error(f"❌ Error inserting translation: {e}")
        raise

def translate_with_vm(text_batch, target_lang, vm_ip = "20.244.31.97", port=8000):
    logger.info(f"🔄 Translating batch of {len(text_batch)} texts using VM-hosted API → {target_lang}")

    # Map the target language to the tokenizer's expected format
    if target_lang not in lang_map:
        logger.error(f"❌ Unsupported target language code: {target_lang}")
        return None
    
    mapped_lang = lang_map[target_lang]  # e.g., 'de-DE' becomes 'de_DE'

    api_url = f"http://{vm_ip}:{port}/translate"
    
    # Prepare the data payload for a batch of texts
    data = {
        "text": text_batch,  # Batch of strings
        "src_lang": "en_XX",  # Assuming the source language is always English (en_XX)
        "tgt_lang": mapped_lang  # Use the mapped language format
    }

    # Make the request to the VM-hosted FastAPI server
    try:
        response = requests.post(api_url, json=data)
        if response.status_code == 200:
            result = response.json()
            translated_texts = result.get("translated_texts", [])
            if translated_texts:
                logger.info(f"✅ Translations successful: {translated_texts[:3]}...")  # Log first 3 translations for brevity
                return translated_texts
            else:
                logger.error(f"❌ No translations returned from API")
                return None
        else:
            logger.error(f"❌ VM API error {response.status_code}: {response.text}")
            return None
    except Exception as e:
        logger.error(f"❌ Error while calling VM API: {e}")
        return None

def process_xlf_file(xlf_path, target_lang, conn, hf_token, vm_ip):
    logger.info(f"🔄 Processing {xlf_path} for {target_lang}")
    tree = ET.parse(xlf_path)
    root = tree.getroot()

    cursor = conn.cursor()
    translated_count = 0
    skipped_count = 0

    text_batch = []
    for tu in root.findall(".//trans-unit"):
        source_elem = tu.find("source")
        target_elem = tu.find("target")
        if source_elem is None or target_elem is None:
            continue
        source_text = source_elem.text or ""
        if not source_text.strip():
            continue

        # 1. Check SQL cache
        cached = query_translation(cursor, source_text, target_lang)
        if cached:
            target_elem.text = cached
            skipped_count += 1
            logger.info(f"⚠️ Using cached translation for: {source_text}")
            continue

        # Add to batch
        text_batch.append(source_text)

        # If batch size is 20, send the batch to the VM
        if len(text_batch) == 20:
            translated_batch = translate_with_vm(text_batch, target_lang, vm_ip)
            if translated_batch:
                for i, translated in enumerate(translated_batch):
                    target_elem = root.findall(".//trans-unit")[i].find("target")
                    target_elem.text = translated
                    insert_translation(cursor, text_batch[i], target_lang, translated)
                translated_count += len(text_batch)
            else:
                skipped_count += len(text_batch)
            text_batch = []  # Reset the batch

    # If there are any remaining texts in the batch (less than 20)
    if text_batch:
        translated_batch = translate_with_vm(text_batch, target_lang, vm_ip)
        if translated_batch:
            for i, translated in enumerate(translated_batch):
                target_elem = root.findall(".//trans-unit")[i].find("target")
                target_elem.text = translated
                insert_translation(cursor, text_batch[i], target_lang, translated)
            translated_count += len(text_batch)
        else:
            skipped_count += len(text_batch)

    conn.commit()
    tree.write(xlf_path, encoding="utf-8", xml_declaration=True)
    logger.info(f"✅ Completed {xlf_path}: {translated_count} new, {skipped_count} cached/skipped")

def main(config_path):
    logger.info("🔄 Starting translation process...")
    config = load_config(config_path)

    storage_conn_str = config["blob_connection_string"]
    raw_container = config["raw_container"]
    translated_container = config["translated_container"]
    sql_conn_str = config["sql_conn_str"]
    hf_token = config["hf_api_token"]
    vm_ip = config["vm_ip"]
    vm_port = config.get("vm_port", 8000)

    blob_service = BlobServiceClient.from_connection_string(storage_conn_str)
    raw_client = blob_service.get_container_client(raw_container)
    trans_client = blob_service.get_container_client(translated_container)
    if not trans_client.exists():
        trans_client.create_container()

    conn = connect_sql(sql_conn_str)

    for blob in raw_client.list_blobs():
        blob_name = blob.name
        local_path = os.path.basename(blob_name)
        with open(local_path, "wb") as f:
            f.write(raw_client.get_blob_client(blob).download_blob().readall())

        parts = blob_name.split(".")
        if len(parts) < 2:
            logger.warning(f"⚠️ Skipping blob {blob_name}, unexpected name format")
            continue
        target_lang = parts[-2]  # e.g. MyFile.resx.fr-FR.xlf → "fr-FR"

        logger.info(f"🌍 Translating {blob_name} → {target_lang}")
        process_xlf_file(local_path, target_lang, conn, hf_token, vm_ip)

        # Upload translated
        trans_client.upload_blob(name=blob_name, data=open(local_path, "rb"), overwrite=True)
        raw_client.delete_blob(blob_name)
        os.remove(local_path)
        logger.info(f"📤 Uploaded translated {blob_name} and removed from raw container")

    conn.close()
    logger.info("🎉 Phase2 translation completed successfully")

if __name__ == "__main__":
    import sys
    if len(sys.argv) != 2:
        logger.error("❌ Usage: python translation.py <config.json>")
        sys.exit(1)
    main(sys.argv[1])
