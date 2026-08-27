import json
import os
import sys
import threading
import time
from urllib.parse import parse_qs, urlparse
import requests

# === CONFIGURATION ===
TELEGRAM_BOT_TOKEN = "8023722837:AAG1YBsFfjzJ-rMemXRCUHtXcbNvxmLomNk"
SUPER_ADMIN_ID = "7130309107"
DEFAULT_MAX_MONITORS = 2  # Default limit set to 2 monitors per person

BASE_DOMAIN = "https://www.vinted.be"
SEARCH_URL = f"{BASE_DOMAIN}/api/v2/catalog/items"

DATA_FILE = "data/monitors.json"
USERS_FILE = "data/users.json"
MSG_TRACK_FILE = "data/messages.json"
DEALS_HISTORY_FILE = "data/deals_history.json"
POLL_INTERVAL = 15.0

headers = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML,"
        " like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "nl-BE,nl;q=0.9,en;q=0.8",
    "Connection": "keep-alive",
}

user_states = {}
active_monitors = {}
paused_chats = {}  # <-- Aangepast naar dictionary: {chat_id: expire_timestamp (of None voor oneindig)}

BOT_START_TIME = time.time()
stats = {
    "total_scanned": 0,
    "deals_found": 0,
}
stats_lock = threading.Lock()
msg_lock = threading.Lock()

GLOBAL_BLACKLIST = [
    "backbone",
    "portal",
    "controller",
    "dualsense",
    "case",
    "hoes",
    "skin",
]


def load_tracked_messages():
  if os.path.exists(MSG_TRACK_FILE):
    try:
      with open(MSG_TRACK_FILE, "r", encoding="utf-8") as f:
        return json.load(f)
    except Exception:
      pass
  return {}


def save_tracked_messages(data):
  try:
    os.makedirs(os.path.dirname(MSG_TRACK_FILE), exist_ok=True)
    with open(MSG_TRACK_FILE, "w", encoding="utf-8") as f:
      json.dump(data, f, indent=2, ensure_ascii=False)
  except Exception as e:
    print(f"[-] Error saving messages: {e}")


def track_msg_id(chat_id, mid):
  with msg_lock:
    chat_id_str = str(chat_id)
    tracked = load_tracked_messages()
    if chat_id_str not in tracked:
      tracked[chat_id_str] = []
    if mid not in tracked[chat_id_str]:
      tracked[chat_id_str].append(mid)
    if len(tracked[chat_id_str]) > 100:
      tracked[chat_id_str].pop(0)
    save_tracked_messages(tracked)


# === DEAL HISTORY FUNCTIES ===
def log_sent_deal(chat_id, item_data):
  chat_id_str = str(chat_id)
  history = {}
  if os.path.exists(DEALS_HISTORY_FILE):
    try:
      with open(DEALS_HISTORY_FILE, "r", encoding="utf-8") as f:
        history = json.load(f)
    except Exception:
      pass
      
  if chat_id_str not in history:
    history[chat_id_str] = []
    
  history[chat_id_str].insert(0, item_data)
  
  if len(history[chat_id_str]) > 10:
    history[chat_id_str].pop()
    
  try:
    os.makedirs(os.path.dirname(DEALS_HISTORY_FILE), exist_ok=True)
    with open(DEALS_HISTORY_FILE, "w", encoding="utf-8") as f:
      json.dump(history, f, indent=2, ensure_ascii=False)
  except Exception as e:
    print(f"[-] Error saving deal history: {e}")


def load_user_deal_history(chat_id):
  chat_id_str = str(chat_id)
  if os.path.exists(DEALS_HISTORY_FILE):
    try:
      with open(DEALS_HISTORY_FILE, "r", encoding="utf-8") as f:
        history = json.load(f)
        return history.get(chat_id_str, [])
    except Exception:
      pass
  return []


def load_known_users():
  users = {}
  if os.path.exists(USERS_FILE):
    try:
      with open(USERS_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
        if isinstance(data, dict):
          for k, v in data.items():
            if isinstance(v, str):
              users[str(k)] = {
                  "name": v,
                  "role": "admin" if str(k) == str(SUPER_ADMIN_ID) else "user",
                  "expires_at": None,
                  "max_monitors": "unlimited" if str(k) == str(SUPER_ADMIN_ID) else DEFAULT_MAX_MONITORS,
                  "setup_done": True
              }
            elif isinstance(v, dict):
              if "expires_at" not in v:
                v["expires_at"] = None
              if "max_monitors" not in v:
                v["max_monitors"] = "unlimited" if str(k) == str(SUPER_ADMIN_ID) else DEFAULT_MAX_MONITORS
              if "setup_done" not in v:
                v["setup_done"] = True
              users[str(k)] = v
        elif isinstance(data, list):
          for item in data:
            users[str(item)] = {
                "name": "User",
                "role": "user",
                "expires_at": None,
                "max_monitors": DEFAULT_MAX_MONITORS,
                "setup_done": True
            }
    except Exception:
      pass

  admin_key = str(SUPER_ADMIN_ID).strip()
  if admin_key not in users:
    users[admin_key] = {
        "name": "Owner",
        "role": "admin",
        "expires_at": None,
        "max_monitors": "unlimited",
        "setup_done": True
    }
  else:
    users[admin_key]["role"] = "admin"
    users[admin_key]["expires_at"] = None
    users[admin_key]["max_monitors"] = "unlimited"
    users[admin_key]["setup_done"] = True

  return users


def save_known_users(users_dict):
  try:
    os.makedirs(os.path.dirname(USERS_FILE), exist_ok=True)
    with open(USERS_FILE, "w", encoding="utf-8") as f:
      json.dump(users_dict, f, indent=2, ensure_ascii=False)
  except Exception as e:
    print(f"[-] Error saving users: {e}")


def is_admin(chat_id):
  cid = str(chat_id).strip()
  if cid == str(SUPER_ADMIN_ID).strip():
    return True
  users = load_known_users()
  u = users.get(cid)
  return u is not None and u.get("role") == "admin"


def register_user(chat_id, name="Pending...", days=None, setup_done=False):
  chat_id_str = str(chat_id).strip()
  users = load_known_users()
  is_owner = (chat_id_str == str(SUPER_ADMIN_ID).strip())
  role = "admin" if is_owner else users.get(chat_id_str, {}).get("role", "user")
  
  expires_at = None
  if days and not is_owner:
    expires_at = time.time() + (days * 86400)

  users[chat_id_str] = {
      "name": name,
      "role": role,
      "expires_at": expires_at,
      "max_monitors": "unlimited" if is_owner else DEFAULT_MAX_MONITORS,
      "setup_done": True if is_owner else setup_done
  }
  save_known_users(users)


def set_user_duration(chat_id, days):
  chat_id_str = str(chat_id).strip()
  if chat_id_str == str(SUPER_ADMIN_ID).strip():
    return None
  users = load_known_users()
  if chat_id_str in users:
    if days is None:
      users[chat_id_str]["expires_at"] = None
    else:
      users[chat_id_str]["expires_at"] = time.time() + (days * 86400)
    save_known_users(users)
    return users[chat_id_str]["expires_at"]
  return None


def set_user_max_monitors(chat_id, val):
  chat_id_str = str(chat_id).strip()
  users = load_known_users()
  if chat_id_str in users:
    users[chat_id_str]["max_monitors"] = val
    save_known_users(users)


def get_user_monitor_limit(chat_id):
  if is_admin(chat_id):
    return "unlimited"
  users = load_known_users()
  udata = users.get(str(chat_id), {})
  limit = udata.get("max_monitors", DEFAULT_MAX_MONITORS)
  return limit


def get_expiry_status_text(expires_at):
  if expires_at is None:
    return "Unlimited"
  remaining = expires_at - time.time()
  if remaining <= 0:
    return "Expired"
  days = int(remaining // 86400)
  hours = int((remaining % 86400) // 3600)
  if days > 1:
    return f"{days} days, {hours} hours"
  elif days == 1:
    return f"1 day, {hours} hours"
  elif hours > 0:
    return f"{hours} hours"
  return "Less than 1 hour"


def check_and_cleanup_expired_users():
  users = load_known_users()
  now = time.time()
  expired_list = []

  for uid, udata in list(users.items()):
    if uid == str(SUPER_ADMIN_ID).strip():
      continue
    exp = udata.get("expires_at")
    if exp and now > exp:
      expired_list.append(uid)

  for uid in expired_list:
    to_del = [k for k, m in active_monitors.items() if m.get("chat_id") == uid]
    for k in to_del:
      active_monitors[k]["event"].set()
      active_monitors.pop(k, None)
    save_active_monitors()

    remove_user(uid)

    try:
      send_msg(uid, "⌛ <b>Access Expired</b>\n\nYour access period for Snipely has expired. Please contact the administrator to renew your access.")
      send_msg(SUPER_ADMIN_ID, f"🔔 <b>User Expired:</b> ID <code>{uid}</code> has been automatically removed due to expired subscription.")
    except Exception:
      pass


def toggle_admin_role(chat_id):
  chat_id_str = str(chat_id).strip()
  if chat_id_str == str(SUPER_ADMIN_ID).strip():
    return False
  users = load_known_users()
  if chat_id_str in users:
    cur_role = users[chat_id_str].get("role", "user")
    new_role = "user" if cur_role == "admin" else "admin"
    users[chat_id_str]["role"] = new_role
    if new_role == "admin":
      users[chat_id_str]["max_monitors"] = "unlimited"
    save_known_users(users)
    return new_role
  return None


def remove_user(chat_id):
  chat_id_str = str(chat_id).strip()
  users = load_known_users()
  if chat_id_str in users and chat_id_str != str(SUPER_ADMIN_ID).strip():
    del users[chat_id_str]
    save_known_users(users)


def delete_single_msg(chat_id, mid):
  url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/deleteMessage"
  try:
    requests.post(url, json={"chat_id": chat_id, "message_id": mid}, timeout=1)
  except Exception:
    pass


def setup_telegram_commands():
  url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/setMyCommands"
  commands = [
      {"command": "menu", "description": "Open the Snipely menu"},
      {"command": "clear", "description": "Clean up chat history"},
      {"command": "stats", "description": "View Snipely performance"},
  ]
  try:
    requests.post(url, json={"commands": commands}, timeout=5)
  except Exception:
    pass


def get_next_available_local_id(chat_id):
  used_ids = set()
  for k, v in active_monitors.items():
    if v.get("chat_id") == str(chat_id):
      used_ids.add(v.get("local_id"))

  candidate = 1
  while candidate in used_ids:
    candidate += 1
  return candidate


def load_saved_monitors():
  if os.path.exists(DATA_FILE):
    try:
      with open(DATA_FILE, "r", encoding="utf-8") as f:
        return json.load(f)
    except Exception as e:
      print(f"[-] Error loading monitors: {e}")
  return {}


def save_active_monitors():
  data = {}
  for k, v in active_monitors.items():
    data[str(k)] = {
        "chat_id": v.get("chat_id", ""),
        "local_id": v.get("local_id", 1),
        "name": v.get("name", f"Monitor #{v.get('local_id', 1)}"),
        "url": v.get("url", ""),
        "price": v.get("price", 0),
        "include_words": v.get("include_words", []),
        "exclude_words": v.get("exclude_words", []),
    }
  try:
    os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
    with open(DATA_FILE, "w", encoding="utf-8") as f:
      json.dump(data, f, indent=2, ensure_ascii=False)
      f.flush()
      os.fsync(f.fileno())
  except Exception as e:
    print(f"[-] Error saving monitors: {e}")


def send_or_edit_msg(chat_id, text, reply_markup=None, message_id=None):
  if message_id:
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/editMessageText"
    payload = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if reply_markup:
      payload["reply_markup"] = reply_markup
    try:
      res = requests.post(url, json=payload, timeout=5).json()
      if res.get("ok"):
        track_msg_id(chat_id, message_id)
        return message_id
      else:
        delete_single_msg(chat_id, message_id)
    except Exception:
      delete_single_msg(chat_id, message_id)

  url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
  payload = {
      "chat_id": chat_id,
      "text": text,
      "parse_mode": "HTML",
      "disable_web_page_preview": True,
  }
  if reply_markup:
    payload["reply_markup"] = reply_markup
  try:
    res = requests.post(url, json=payload, timeout=10).json()
    if res.get("ok"):
      mid = res["result"]["message_id"]
      track_msg_id(chat_id, mid)
      return mid
  except Exception:
    pass
  return None


def send_msg(chat_id, text, reply_markup=None):
  return send_or_edit_msg(chat_id, text, reply_markup=reply_markup)


def answer_callback(callback_query_id, text=None):
  url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/answerCallbackQuery"
  payload = {"callback_query_id": callback_query_id}
  if text:
    payload["text"] = text
  try:
    requests.post(url, json=payload, timeout=5)
  except Exception:
    pass


def get_accurate_seller_info(session, user_dict):
  username = user_dict.get("login", "Unknown")
  user_id = user_dict.get("id")
  feedback_count = user_dict.get("feedback_count")
  feedback_rep = user_dict.get("feedback_reputation")

  if (feedback_count is None or feedback_count == 0) and user_id:
    try:
      u_url = f"{BASE_DOMAIN}/api/v2/users/{user_id}"
      u_resp = session.get(u_url, timeout=5)
      if u_resp.status_code == 200:
        u_data = u_resp.json().get("user", {})
        feedback_count = u_data.get("feedback_count", 0)
        feedback_rep = u_data.get("feedback_reputation", 0)
    except Exception:
      pass

  feedback_count = feedback_count or 0
  score = 0.0

  if feedback_rep:
    score = float(feedback_rep)
    score = round(score * 5 if score <= 1.0 else score, 1)

  if feedback_count == 0:
    seller_status = "⚠️ 0 reviews (New account)"
    risk_tag = (
        "\n\n🔴 <b>RISK: NEW ACCOUNT (0 reviews)</b>\n<i>Only pay via the"
        " Vinted button.</i>"
    )
  elif score > 0 and score < 3.0:
    seller_status = f"❌ {score}/5.0 ({feedback_count} reviews)"
    risk_tag = (
        f"\n\n🔴 <b>HIGH RISK: LOW SCORE ({score}/5.0)</b>\n<i>Seller rating"
        " is below 3 stars!</i>"
    )
  elif feedback_count <= 3 or score < 4.0:
    seller_status = f"⭐️ {score}/5.0 ({feedback_count} reviews)"
    risk_tag = (
        f"\n\n🟠 <b>RISK: FEW / AVERAGE REVIEWS</b>\n<i>{feedback_count}"
        f" review(s) with score {score}/5.0.</i>"
    )
  else:
    seller_status = f"⭐️ {score}/5.0 ({feedback_count} reviews)"
    risk_tag = "\n\n🟢 <b>TRUSTED PROFILE</b>"

  profile_url = f"{BASE_DOMAIN}/member/{user_id}" if user_id else BASE_DOMAIN
  return username, seller_status, risk_tag, profile_url


# === STANDALONE DEAL NOTIFICATION (DOES NOT DISRUPT MENU) ===
def send_telegram_alert(session, chat_id, item, local_id, name):
  with stats_lock:
    stats["deals_found"] += 1

  title = item.get("title", "No title")
  raw_price = item.get("price", {}).get("amount", "0")
  currency = item.get("price", {}).get("currency_code", "EUR")
  url = f"{BASE_DOMAIN}{item.get('path', '')}"
  photo_url = item.get("photo", {}).get("url", "")
  brand = item.get("brand_title", "Unknown")

  user_dict = item.get("user", {})
  username, seller_status, risk_tag, profile_url = get_accurate_seller_info(
      session, user_dict
  )

  log_sent_deal(chat_id, {
      "title": title,
      "price": f"€{raw_price} {currency}",
      "brand": brand,
      "url": url,
      "monitor_name": name,
      "local_id": local_id
  })

  caption = (
      f"🚨 <b>Snipely Deal: [{name}]</b> (ID #{local_id})\n\n"
      f"📦 <b>Title:</b> {title}\n"
      f"💰 <b>Price:</b> €{raw_price} {currency}\n"
      f"🏷 <b>Brand:</b> {brand}\n"
      f"👤 <b>Seller:</b> {username}\n"
      f"📊 <b>Rating:</b> {seller_status}"
      f"{risk_tag}"
  )

  deal_keyboard = {
      "inline_keyboard": [
          [{"text": "🛍️ Open in Vinted App", "url": url}],
          [{"text": f"👤 Profile: {username}", "url": profile_url}],
      ]
  }

  try:
    if photo_url:
      res = requests.post(
          f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto",
          json={
              "chat_id": chat_id,
              "photo": photo_url,
              "caption": caption,
              "parse_mode": "HTML",
              "reply_markup": deal_keyboard,
          },
          timeout=10,
      ).json()
      if res.get("ok"):
        track_msg_id(chat_id, res["result"]["message_id"])
    else:
      url_msg = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
      res = requests.post(
          url_msg,
          json={
              "chat_id": chat_id,
              "text": caption,
              "parse_mode": "HTML",
              "disable_web_page_preview": True,
              "reply_markup": deal_keyboard
          },
          timeout=10
      ).json()
      if res.get("ok"):
        track_msg_id(chat_id, res["result"]["message_id"])
  except Exception as e:
    print(f"[-] Telegram alert error: {e}")


def is_matching_item(item, include_words, exclude_words):
  title = item.get("title", "").lower()
  brand = item.get("brand_title", "").lower()
  combined = f"{title} {brand}"

  if include_words and not any(req in combined for req in include_words):
    return False

  if exclude_words:
    for bad in exclude_words:
      if bad in combined:
        return False

  for g_bad in GLOBAL_BLACKLIST:
    if g_bad in combined and g_bad not in include_words:
      return False

  return True


def refresh_session(session):
  try:
    session.cookies.clear()
    session.get(BASE_DOMAIN, timeout=10)
  except Exception:
    pass


def monitor_task(
    chat_id, local_id, name, params, include_words, exclude_words, stop_event
):
  session = requests.Session()
  session.headers.update(headers)
  refresh_session(session)

  seen_ids = set()
  try:
    init_resp = session.get(SEARCH_URL, params=params, timeout=10)
    if init_resp.status_code == 200:
      for it in init_resp.json().get("items", []):
        seen_ids.add(it.get("id"))
  except Exception:
    pass

  print(f"[+] Monitor #{local_id} ({name}) started for {chat_id}.")

  while not stop_event.is_set():
    # Controleer of gebruiker gepauzeerd staat én of de snooze-tijd nog geldig is
    chat_id_str = str(chat_id)
    is_currently_paused = False
    if chat_id_str in paused_chats:
      expire_time = paused_chats[chat_id_str]
      if expire_time is None or time.time() < expire_time:
        is_currently_paused = True
      else:
        # Snooze tijd is verstreken, automatisch verwijderen uit paused_chats
        paused_chats.pop(chat_id_str, None)

    if not is_currently_paused:
      try:
        resp = session.get(SEARCH_URL, params=params, timeout=10)

        if resp.status_code in (401, 403):
          refresh_session(session)
          time.sleep(5)
          continue

        if resp.status_code == 200:
          items = resp.json().get("items", [])
          with stats_lock:
            stats["total_scanned"] += len(items)

          for item in items:
            item_id = item.get("id")
            if item_id and item_id not in seen_ids:
              seen_ids.add(item_id)
              if is_matching_item(
                  item, include_words, exclude_words
              ):
                send_telegram_alert(
                    session, chat_id, item, local_id, name
                )
                print(
                    f"[+] Deal [{name}] -> {chat_id}:"
                    f" {item.get('title')}"
                )

      except Exception as e:
        print(f"[-] Error in monitor #{local_id}: {e}")

    stop_event.wait(POLL_INTERVAL)

  print(f"[-] Monitor #{local_id} terminated.")


def extract_params(url_str, max_price):
  parsed = urlparse(url_str)
  query_params = parse_qs(parsed.query)
  api_price_limit = str(float(max_price) * 1.1)

  params_list = [
      ("order", "newest_first"),
      ("price_to", api_price_limit),
      ("per_page", "20"),
  ]

  for key, values in query_params.items():
    clean_vals = [v.strip() for v in values if v.strip()]
    if not clean_vals or key in [
        "search_id",
        "order",
        "price_to",
        "page",
        "time",
        "search_by_image_uuid",
        "search_by_image_id",
    ]:
      continue

    if "video_game_platform" in key:
      for v in clean_vals:
        params_list.append(("video_game_platform_ids[]", v))
      continue

    if "catalog" in key:
      for v in clean_vals:
        params_list.append(("catalog_ids[]", v))
      continue

    if "brand" in key:
      for v in clean_vals:
        params_list.append(("brand_ids[]", v))
      continue

    for val in clean_vals:
      param_name = key if key.endswith("[]") else f"{key}[]"
      if key == "search_text":
        param_name = "search_text"
      params_list.append((param_name, val))

  return params_list


def start_monitor_instance(
    chat_id, local_id, name, target_url, price, include_words, exclude_words
):
  unique_key = f"{chat_id}_{local_id}"
  stop_event = threading.Event()
  params = extract_params(target_url, price)

  active_monitors[unique_key] = {
      "chat_id": str(chat_id),
      "local_id": int(local_id),
      "name": name,
      "event": stop_event,
      "url": target_url,
      "price": price,
      "include_words": include_words,
      "exclude_words": exclude_words,
  }

  threading.Thread(
      target=monitor_task,
      args=(
          chat_id,
          local_id,
          name,
          params,
          include_words,
          exclude_words,
          stop_event,
      ),
      daemon=True,
  ).start()


def restart_monitor_with_new_settings(unique_key):
  m = active_monitors[unique_key]
  m["event"].set()
  start_monitor_instance(
      m["chat_id"],
      m["local_id"],
      m["name"],
      m["url"],
      m["price"],
      m["include_words"],
      m["exclude_words"],
  )
  save_active_monitors()


def clear_chat_messages(chat_id, count=None):
  del_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/deleteMessage"
  with msg_lock:
    cid = str(chat_id)
    tracked = load_tracked_messages()
    msgs = tracked.get(cid, [])
    to_delete = msgs[-count:] if count else msgs[:]
    
    def delete_batch(mids):
      for mid in mids:
        try:
          requests.post(del_url, json={"chat_id": chat_id, "message_id": mid}, timeout=1)
        except Exception:
          pass

    threading.Thread(target=delete_batch, args=(to_delete,), daemon=True).start()
    
    deleted_count = len(to_delete)
    tracked[cid] = [m for m in msgs if m not in to_delete]
    save_tracked_messages(tracked)
    return deleted_count


def shutdown_bot(trigger_chat_id):
  users = load_known_users()
  offline_msg = (
      "⚠️ <b>Snipely is now OFFLINE</b>\n\n"
      "The bot has been shut down for maintenance or restart. Your monitors remain saved.\n"
      "<i>You will automatically receive the start menu once the bot comes back online!</i>"
  )
  
  for u in users.keys():
    try:
      res = requests.post(
          f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
          json={"chat_id": u, "text": offline_msg, "parse_mode": "HTML"},
          timeout=5
      ).json()
      if res.get("ok"):
        track_msg_id(u, res["result"]["message_id"])
    except Exception:
      pass

  for m_data in active_monitors.values():
    m_data["event"].set()

  time.sleep(2)
  os._exit(0)


# === DASHBOARDS ===


def send_main_dashboard(chat_id, is_startup=False, message_id=None):
  users = load_known_users()
  udata = users.get(str(chat_id), {})
  discord_id = udata.get("name", "Not set")
  expires_at = udata.get("expires_at")
  expiry_str = get_expiry_status_text(expires_at)
  limit_val = get_user_monitor_limit(chat_id)

  user_monitors = [
      m for m in active_monitors.values() if m.get("chat_id") == str(chat_id)
  ]
  count = len(user_monitors)
  limit_str = "Unlimited" if limit_val == "unlimited" else str(limit_val)

  # Check of gebruiker gepauzeerd staat en of de snooze nog actief is
  chat_id_str = str(chat_id)
  is_user_paused = False
  if chat_id_str in paused_chats:
    expire_time = paused_chats[chat_id_str]
    if expire_time is None or time.time() < expire_time:
      is_user_paused = True
    else:
      paused_chats.pop(chat_id_str, None)

  pause_btn_text = "▶️ Resume Scanning" if is_user_paused else "⏸️ Pause Scanning"
  pause_action = "menu_snooze_menu" if not is_user_paused else "menu_resume"

  kb_rows = [
      [
          {
              "text": f"📋 My Monitors ({count}/{limit_str})",
              "callback_data": "menu_list",
          },
          {
              "text": "➕ New Monitor",
              "callback_data": "menu_add_start",
          },
      ],
      [
          {
              "text": "✏️ Quick Edit",
              "callback_data": "menu_edit_select",
          },
          {
              "text": "🗑️ Delete Monitor",
              "callback_data": "menu_delete_select",
          },
      ],
      [
          {"text": pause_btn_text, "callback_data": pause_action},
          {"text": "📊 Statistics", "callback_data": "menu_stats"},
      ],
      [
          {"text": "📜 Deal History", "callback_data": "menu_history"},
          {"text": "🧹 Clean Chat", "callback_data": "menu_clear"},
      ],
      [
          {"text": "❓ Help & Guide", "callback_data": "menu_help"},
      ],
  ]

  if is_admin(chat_id):
    kb_rows.append([
        {
            "text": "👑 Admin Panel",
            "callback_data": "admin_panel",
        }
    ])

  search_status_str = "⏸️ Paused" if is_user_paused else "🟢 Active"

  header = (
      "🟢 <b>Bot Server: Online</b>\n\n"
      "⚡ <b>Snipely</b> — <i>Vinted Deals & Sniper</i>\n\n"
      f"👤 <b>Discord ID:</b> <code>{discord_id}</code>\n"
      f"📡 <b>Search Status:</b> {search_status_str}\n"
      f"⏳ <b>Access:</b> {expiry_str}\n"
      f"📦 <b>Active Monitors:</b> {count} / {limit_str}\n\n"
      "<i>Choose an option below:</i>"
  )

  return send_or_edit_msg(chat_id, header, reply_markup={"inline_keyboard": kb_rows}, message_id=message_id)


def send_snooze_menu(chat_id, message_id=None):
  text = "⏱️ <b>Select Pause / Snooze Duration:</b>\n\n<i>Choose how long you want to pause your searches:</i>"
  keyboard = {
      "inline_keyboard": [
          [
              {"text": "⏱️ 30 Minutes", "callback_data": "snooze_30"},
              {"text": "⏱️ 1 Hour", "callback_data": "snooze_60"},
          ],
          [
              {"text": "⏱️ 3 Hours", "callback_data": "snooze_180"},
              {"text": "♾️ Manual (Until Resume)", "callback_data": "snooze_manual"},
          ],
          [{"text": "⬅️ Back to Main Menu", "callback_data": "menu_main"}],
      ]
  }
  return send_or_edit_msg(chat_id, text, reply_markup=keyboard, message_id=message_id)


def send_admin_panel(chat_id, message_id=None):
  users = load_known_users()
  total_users = len(users)
  total_active_monitors = len(active_monitors)

  text = (
      "👑 <b>Snipely Admin Management Panel</b>\n\n"
      f"👥 <b>Total Users:</b> {total_users}\n"
      f"🎯 <b>Total Active Monitors:</b> {total_active_monitors}\n\n"
      "<i>What would you like to manage?</i>"
  )

  keyboard = {
      "inline_keyboard": [
          [{"text": "➕ Add New User", "callback_data": "admin_add_new_user"}],
          [{"text": "👥 Users & Discord IDs", "callback_data": "admin_view_users"}],
          [{"text": "📢 Broadcast Message", "callback_data": "admin_broadcast_msg"}],
          [{"text": "🔌 Shutdown Bot (/kill)", "callback_data": "admin_do_kill"}],
          [{"text": "⬅️ Back to Main Menu", "callback_data": "menu_main"}],
      ]
  }
  return send_or_edit_msg(chat_id, text, reply_markup=keyboard, message_id=message_id)


def send_admin_users_list(chat_id, message_id=None):
  users = load_known_users()
  keyboard = []

  for uid, udata in users.items():
    uname = udata.get("name", "Unknown")
    urole = udata.get("role", "user")
    setup_done = udata.get("setup_done", True)
    exp_str = get_expiry_status_text(udata.get("expires_at"))
    
    status_icon = "⏳ " if not setup_done else ""
    role_icon = "👑 " if uid == str(SUPER_ADMIN_ID).strip() else ("⭐ " if urole == "admin" else "👤 ")
    btn_text = f"{role_icon}{status_icon}{uname} ({exp_str})"
    keyboard.append([{"text": btn_text, "callback_data": f"admin_user_manage_{uid}"}])

  keyboard.append([{"text": "⬅️ Back to Admin Panel", "callback_data": "admin_panel"}])
  return send_or_edit_msg(
      chat_id,
      "👥 <b>User Overview (Discord IDs):</b>\n<i>Click a user to modify their time or ID:</i>",
      reply_markup={"inline_keyboard": keyboard},
      message_id=message_id,
  )


def send_admin_manage_single_user(chat_id, target_user_id, message_id=None):
  users = load_known_users()
  udata = users.get(str(target_user_id), {})
  uname = udata.get("name", "Unknown")
  urole = udata.get("role", "user")
  setup_done = udata.get("setup_done", True)
  exp_str = get_expiry_status_text(udata.get("expires_at"))
  limit_val = udata.get("max_monitors", DEFAULT_MAX_MONITORS)
  limit_str = "Unlimited" if limit_val == "unlimited" else str(limit_val)
  user_monitors = [m for m in active_monitors.values() if m.get("chat_id") == str(target_user_id)]

  role_str = "👑 Owner" if str(target_user_id) == str(SUPER_ADMIN_ID).strip() else ("⭐ Admin" if urole == "admin" else "👤 User")
  setup_str = "🟢 Active" if setup_done else "🟡 Waiting for Discord ID"

  text = (
      f"👤 <b>User Management</b>\n\n"
      f"🎮 <b>Discord ID:</b> <code>{uname}</code>\n"
      f"💬 <b>Telegram ID:</b> <code>{target_user_id}</code>\n"
      f"📊 <b>Status:</b> {setup_str}\n"
      f"🛡 <b>Role:</b> {role_str}\n"
      f"⏳ <b>Remaining Access:</b> <b>{exp_str}</b>\n"
      f"⚙️ <b>Max Monitors:</b> <b>{limit_str}</b>\n"
      f"📦 <b>Current Monitors:</b> {len(user_monitors)}\n"
  )

  keyboard = []
  if str(target_user_id) != str(SUPER_ADMIN_ID).strip():
    keyboard.append([{"text": "⏱️ Adjust Duration", "callback_data": f"admin_set_time_{target_user_id}"}])
    keyboard.append([{"text": "🔢 Adjust Max Monitors", "callback_data": f"admin_set_limit_{target_user_id}"}])
    keyboard.append([{"text": "✏️ Edit Discord ID", "callback_data": f"admin_edit_discord_{target_user_id}"}])
    if urole == "admin":
      keyboard.append([{"text": "👤 Make Regular User", "callback_data": f"admin_toggle_role_{target_user_id}"}])
    else:
      keyboard.append([{"text": "⭐ Make Admin", "callback_data": f"admin_toggle_role_{target_user_id}"}])

    keyboard.append([{"text": "🗑️ Delete User", "callback_data": f"admin_remove_user_{target_user_id}"}])
    keyboard.append([{"text": "🛑 Clear All Monitors", "callback_data": f"admin_clear_monitors_{target_user_id}"}])
  else:
    text += "\n<i>This is your own Super-Admin account.</i>"

  keyboard.append([{"text": "⬅️ Back to User List", "callback_data": "admin_view_users"}])
  return send_or_edit_msg(chat_id, text, reply_markup={"inline_keyboard": keyboard}, message_id=message_id)


def send_admin_time_selector(chat_id, target_user_id, message_id=None):
  users = load_known_users()
  uname = users.get(str(target_user_id), {}).get("name", "User")

  text = f"⏱️ <b>Choose access duration for ID <code>{target_user_id}</code> ({uname}):</b>"

  keyboard = {
      "inline_keyboard": [
          [
              {"text": "⏱️ 1 Week", "callback_data": f"time_set_{target_user_id}_7"},
              {"text": "⏱️ 1 Month", "callback_data": f"time_set_{target_user_id}_30"},
          ],
          [
              {"text": "⏱️ 3 Months", "callback_data": f"time_set_{target_user_id}_90"},
              {"text": "⏱️ 1 Year", "callback_data": f"time_set_{target_user_id}_365"},
          ],
          [{"text": "♾️ Unlimited", "callback_data": f"time_set_{target_user_id}_unlimited"}],
          [{"text": "⬅️ Back to User", "callback_data": f"admin_user_manage_{target_user_id}"}],
      ]
  }
  return send_or_edit_msg(chat_id, text, reply_markup=keyboard, message_id=message_id)


def send_user_monitors_list(chat_id, message_id=None):
  user_monitors = [
      m for m in active_monitors.values() if m.get("chat_id") == str(chat_id)
  ]
  limit_val = get_user_monitor_limit(chat_id)
  can_add = True
  if limit_val != "unlimited" and len(user_monitors) >= int(limit_val) and not is_admin(chat_id):
    can_add = False

  if not user_monitors:
    keyboard = {
        "inline_keyboard": [
            [{
                "text": "➕ Create New Monitor",
                "callback_data": "menu_add_start",
            }],
            [{"text": "⬅️ Back to Main Menu", "callback_data": "menu_main"}],
        ]
    }
    return send_or_edit_msg(
        chat_id,
        "ℹ️ You do not have any active monitors configured.",
        reply_markup=keyboard,
        message_id=message_id,
    )

  user_monitors.sort(key=lambda x: x.get("local_id", 0))
  keyboard = []

  for m in user_monitors:
    lid = m["local_id"]
    keyboard.append([{
        "text": f"⚙️ #{lid}: {m['name']} (€{m['price']})",
        "callback_data": f"open_edit_{lid}",
    }])

  if can_add:
    keyboard.append(
        [{"text": "➕ New Monitor", "callback_data": "menu_add_start"}]
    )
  keyboard.append(
      [{"text": "⬅️ Back to Main Menu", "callback_data": "menu_main"}]
  )

  limit_str = "Unlimited" if limit_val == "unlimited" else str(limit_val)
  return send_or_edit_msg(
      chat_id,
      f"📋 <b>Your Monitors ({len(user_monitors)}/{limit_str}):</b>\n<i>Click a monitor to edit:</i>",
      reply_markup={"inline_keyboard": keyboard},
      message_id=message_id,
  )


def send_selection_list(chat_id, action_type="edit", message_id=None):
  user_monitors = [
      m for m in active_monitors.values() if m.get("chat_id") == str(chat_id)
  ]
  if not user_monitors:
    keyboard = {
        "inline_keyboard": [
            [{"text": "➕ Create New Monitor", "callback_data": "menu_add_start"}],
            [{"text": "⬅️ Back to Main Menu", "callback_data": "menu_main"}],
        ]
    }
    return send_or_edit_msg(
        chat_id,
        "ℹ️ <b>You currently have no active monitors.</b>\n\nClick below to start a new search:",
        reply_markup=keyboard,
        message_id=message_id,
    )

  user_monitors.sort(key=lambda x: x.get("local_id", 0))
  keyboard = []

  for m in user_monitors:
    lid = m["local_id"]
    if action_type == "delete":
      keyboard.append([{
          "text": f"🗑️ Delete #{lid}: {m['name']} (€{m['price']})",
          "callback_data": f"confirm_del_{lid}",
      }])
    else:
      keyboard.append([{
          "text": f"✏️ Edit #{lid}: {m['name']} (€{m['price']})",
          "callback_data": f"open_edit_{lid}",
      }])

  keyboard.append(
      [{"text": "⬅️ Back to Main Menu", "callback_data": "menu_main"}]
  )

  titel = (
      "🗑️ <b>Choose which monitor to delete:</b>"
      if action_type == "delete"
      else "✏️ <b>Choose which monitor to edit:</b>"
  )
  return send_or_edit_msg(chat_id, titel, reply_markup={"inline_keyboard": keyboard}, message_id=message_id)


def send_monitor_edit_panel(chat_id, local_id, message_id=None):
  key = f"{chat_id}_{local_id}"
  if key not in active_monitors:
    return send_user_monitors_list(chat_id, message_id=message_id)

  m = active_monitors[key]
  inc = (
      ", ".join(m["include_words"])
      if m["include_words"]
      else "All"
  )
  exc = ", ".join(m["exclude_words"]) if m["exclude_words"] else "None"

  text = (
      f"⚙️ <b>Manage Monitor #{local_id}</b>\n\n"
      f"📦 <b>Name:</b> {m['name']}\n"
      f"💰 <b>Max Price:</b> <b>€{m['price']}</b>\n"
      f"🔍 <b>Required:</b> <code>{inc}</code>\n"
      f"🚫 <b>Forbidden:</b> <code>{exc}</code>\n\n"
      f"<i>Choose an option to modify:</i>"
  )

  keyboard = {
      "inline_keyboard": [
          [
              {
                  "text": "- €50",
                  "callback_data": f"quick_price_{local_id}_-50",
              },
              {
                  "text": "- €10",
                  "callback_data": f"quick_price_{local_id}_-10",
              },
              {
                  "text": "+ €10",
                  "callback_data": f"quick_price_{local_id}_+10",
              },
              {
                  "text": "+ €50",
                  "callback_data": f"quick_price_{local_id}_+50",
              },
          ],
          [{
              "text": "💰 Type Custom Amount",
              "callback_data": f"set_price_{local_id}",
          }],
          [{
              "text": "🔍 Required Words",
              "callback_data": f"set_inc_{local_id}",
          }],
          [{
              "text": "🚫 Forbidden Words",
              "callback_data": f"set_exc_{local_id}",
          }],
          [{
              "text": "🗑️ Delete this Monitor",
              "callback_data": f"confirm_del_{local_id}",
          }],
          [{"text": "⬅️ Back to Main Menu", "callback_data": "menu_main"}],
      ]
  }
  return send_or_edit_msg(chat_id, text, reply_markup=keyboard, message_id=message_id)


def get_uptime_str():
  delta = int(time.time() - BOT_START_TIME)
  hours, rem = divmod(delta, 3600)
  mins, secs = divmod(rem, 60)
  return f"{hours}h {mins}m {secs}s"


def broadcast_startup():
  recipients = load_known_users()
  for uid in recipients.keys():
    try:
      clear_chat_messages(uid)
      send_main_dashboard(uid, is_startup=True)
    except Exception:
      pass


def listen():
  offset = None
  session = requests.Session()

  setup_telegram_commands()

  try:
    init_updates = session.get(
        f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates",
        params={"offset": -1, "timeout": 5},
        timeout=10,
    ).json()
    if init_updates.get("result"):
      offset = init_updates["result"][-1]["update_id"] + 1
  except Exception:
    pass

  saved = load_saved_monitors()
  if saved:
    for k, data in saved.items():
      user_target_id = str(data.get("chat_id", SUPER_ADMIN_ID))
      local_id = data.get("local_id", 1)
      start_monitor_instance(
          user_target_id,
          local_id,
          data.get("name", f"Search #{local_id}"),
          data["url"],
          data["price"],
          data["include_words"],
          data["exclude_words"],
      )

  broadcast_startup()
  print("[+] Snipely has started and cleared old offline notifications...")

  last_expiry_check = 0

  while True:
    try:
      if time.time() - last_expiry_check > 60:
        check_and_cleanup_expired_users()
        last_expiry_check = time.time()

      url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
      params = {"timeout": 20, "offset": offset}
      res = session.get(url, params=params, timeout=25).json()

      for update in res.get("result", []):
        offset = update["update_id"] + 1

        # === BUTTON INTERACTIONS ===
        if "callback_query" in update:
          cb = update["callback_query"]
          cb_id = cb["id"]
          data = cb.get("data", "")
          chat_id = str(cb["message"]["chat"]["id"])
          mid = cb["message"]["message_id"]

          known_users = load_known_users()
          
          if chat_id not in known_users and str(chat_id) != str(SUPER_ADMIN_ID).strip():
            answer_callback(cb_id)
            send_or_edit_msg(chat_id, f"⛔ <b>No access to Snipely.</b>\n\nYour Telegram ID is: <code>{chat_id}</code>\nAsk the administrator to add this ID to the system.", message_id=mid)
            continue

          udata = known_users.get(chat_id, {})
          if not udata.get("setup_done", True) and str(chat_id) != str(SUPER_ADMIN_ID).strip():
            user_states[chat_id] = {"stage": "waiting_discord_id", "menu_mid": mid}
            answer_callback(cb_id)
            send_or_edit_msg(chat_id, "🎮 <b>Welcome to Snipely!</b>\n\nBefore you can start, please enter your unique <b>Discord ID</b> (17 to 20 digits):", message_id=mid)
            continue

          answer_callback(cb_id)

          if data == "menu_main":
            send_main_dashboard(chat_id, message_id=mid)

          elif data == "menu_list":
            send_user_monitors_list(chat_id, message_id=mid)

          elif data == "menu_edit_select":
            send_selection_list(chat_id, action_type="edit", message_id=mid)

          elif data == "menu_delete_select":
            send_selection_list(chat_id, action_type="delete", message_id=mid)

          elif data == "menu_snooze_menu":
            send_snooze_menu(chat_id, message_id=mid)

          elif data.startswith("snooze_"):
            snooze_type = data.split("_")[1]
            if snooze_type == "30":
              paused_chats[chat_id] = time.time() + (30 * 60)
            elif snooze_type == "60":
              paused_chats[chat_id] = time.time() + (60 * 60)
            elif snooze_type == "180":
              paused_chats[chat_id] = time.time() + (180 * 60)
            elif snooze_type == "manual":
              paused_chats[chat_id] = None  # Oneindig tot handmatige hervatting
            send_main_dashboard(chat_id, message_id=mid)

          elif data == "menu_resume":
            paused_chats.pop(chat_id, None)
            send_main_dashboard(chat_id, message_id=mid)

          elif data == "menu_history":
            history_list = load_user_deal_history(chat_id)
            if not history_list:
              hist_text = "📜 <b>Deal History</b>\n\n<i>No deals have been logged yet for your monitors.</i>"
            else:
              hist_text = "📜 <b>Recent Deal History (Last 10):</b>\n\n"
              for idx, deal in enumerate(history_list, 1):
                hist_text += f"{idx}. <b>{deal.get('title')}</b>\n   💰 {deal.get('price')} | 🏷 {deal.get('brand')}\n   🔗 <a href='{deal.get('url')}'>Open Deal</a> [Monitor: {deal.get('monitor_name')}]\n\n"
            
            keyboard = {
                "inline_keyboard": [
                    [{"text": "⬅️ Back to Main Menu", "callback_data": "menu_main"}]
                ]
            }
            send_or_edit_msg(chat_id, hist_text, reply_markup=keyboard, message_id=mid)

          elif data == "menu_clear":
            clear_chat_messages(chat_id)
            conf_m = send_msg(chat_id, "🧹 <b>Chat cleared!</b>")
            def quick_return():
              time.sleep(2)
              if conf_m:
                delete_single_msg(chat_id, conf_m)
              send_main_dashboard(chat_id)
            threading.Thread(target=quick_return, daemon=True).start()

          elif data == "menu_help":
            help_text = (
                "💡 <b>How does Snipely work?</b>\n\n"
                "1️⃣ <b>Add:</b> Click '➕ New Monitor' and paste your Vinted link.\n"
                "2️⃣ <b>Edit:</b> Click '✏️ Quick Edit' to adjust your prices with +€10/-€10.\n"
                "3️⃣ <b>Alerts:</b> As soon as a deal drops, you instantly receive a photo with purchase link.\n"
                "4️⃣ <b>Cleanup:</b> Use '🧹 Clean Chat' to keep your chat tidy."
            )
            keyboard = {
                "inline_keyboard": [
                    [{"text": "⬅️ Back", "callback_data": "menu_main"}]
                ]
            }
            send_or_edit_msg(chat_id, help_text, reply_markup=keyboard, message_id=mid)

          elif data == "menu_stats":
            with stats_lock:
              scanned = stats["total_scanned"]
              found = stats["deals_found"]
            user_cnt = len([
                m
                for m in active_monitors.values()
                if m.get("chat_id") == chat_id
            ])
            
            is_user_paused = False
            if chat_id in paused_chats:
              expire_time = paused_chats[chat_id]
              if expire_time is None or time.time() < expire_time:
                is_user_paused = True

            stats_msg = (
                "📊 <b>Snipely Statistics</b>\n\n"
                f"⏱ <b>Uptime:</b> {get_uptime_str()}\n"
                f"📡 <b>Search Status:</b> {'⏸️ Paused' if is_user_paused else '🟢 Active'}\n"
                f"🎯 <b>Your Monitors:</b> {user_cnt}\n"
                f"🔍 <b>Scanned Items:</b> {scanned}\n"
                f"🚨 <b>Deals Forwarded:</b> {found}\n"
                f"⚡ <b>Scan Interval:</b> {POLL_INTERVAL}s"
            )
            keyboard = {
                "inline_keyboard": [
                    [{"text": "⬅️ Back", "callback_data": "menu_main"}]
                ]
            }
            send_or_edit_msg(chat_id, stats_msg, reply_markup=keyboard, message_id=mid)

          elif data == "menu_add_start":
            user_monitors_count = len([m for m in active_monitors.values() if m.get("chat_id") == str(chat_id)])
            limit_val = get_user_monitor_limit(chat_id)
            if limit_val != "unlimited" and user_monitors_count >= int(limit_val) and not is_admin(chat_id):
              answer_callback(cb_id, f"⚠️ Maximum of {limit_val} monitors reached!")
              send_or_edit_msg(chat_id, f"⚠️ <b>Limit Reached</b>\n\nYou have reached the maximum limit of <b>{limit_val} active monitors</b>. Delete an old monitor before adding a new one.", reply_markup={"inline_keyboard": [[{"text": "⬅️ Back", "callback_data": "menu_main"}]]}, message_id=mid)
              continue

            user_states[chat_id] = {"stage": "waiting_url", "menu_mid": mid}
            kb = {
                "inline_keyboard": [[{
                    "text": "❌ Cancel",
                    "callback_data": "menu_main",
                }]]
            }
            send_or_edit_msg(
                chat_id,
                "🔗 <b>Step 1 of 4:</b> Paste the <b>Vinted search link</b> here:",
                reply_markup=kb,
                message_id=mid,
            )

          # === ADMIN CALLBACKS ===
          elif data == "admin_panel" and is_admin(chat_id):
            send_admin_panel(chat_id, message_id=mid)

          elif data == "admin_add_new_user" and is_admin(chat_id):
            user_states[chat_id] = {"stage": "admin_waiting_new_user_id", "menu_mid": mid}
            kb = {"inline_keyboard": [[{
                "text": "❌ Cancel",
                "callback_data": "admin_panel",
            }]]}
            send_or_edit_msg(
                chat_id,
                "➕ <b>Enter the Telegram Chat ID of the customer:</b>\n<i>(Numbers only. Once the customer starts the bot, it will automatically ask for their Discord ID.)</i>",
                reply_markup=kb,
                message_id=mid
            )

          elif data == "admin_view_users" and is_admin(chat_id):
            send_admin_users_list(chat_id, message_id=mid)

          elif data.startswith("admin_user_manage_") and is_admin(chat_id):
            target_uid = data.split("_")[3]
            send_admin_manage_single_user(chat_id, target_uid, message_id=mid)

          elif data.startswith("admin_set_time_") and is_admin(chat_id):
            target_uid = data.split("_")[3]
            send_admin_time_selector(chat_id, target_uid, message_id=mid)

          elif data.startswith("admin_set_limit_") and is_admin(chat_id):
            target_uid = data.split("_")[3]
            user_states[chat_id] = {"stage": "admin_editing_limit", "target_uid": target_uid, "menu_mid": mid}
            kb = {"inline_keyboard": [[{"text": "⬅️ Cancel", "callback_data": f"admin_user_manage_{target_uid}"}]]}
            send_or_edit_msg(chat_id, f"🔢 Type the <b>new maximum monitor limit</b> for ID <code>{target_uid}</code> (or type <code>unlimited</code>):", reply_markup=kb, message_id=mid)

          elif data.startswith("admin_edit_discord_") and is_admin(chat_id):
            target_uid = data.split("_")[3]
            user_states[chat_id] = {"stage": "admin_editing_discord_id", "target_uid": target_uid, "menu_mid": mid}
            kb = {"inline_keyboard": [[{"text": "⬅️ Cancel", "callback_data": f"admin_user_manage_{target_uid}"}]]}
            send_or_edit_msg(chat_id, f"✏️ Type the valid <b>Discord ID</b> (17-20 digits) for ID <code>{target_uid}</code>:", reply_markup=kb, message_id=mid)

          elif data.startswith("time_set_") and is_admin(chat_id):
            parts = data.split("_")
            target_uid = parts[2]
            val = parts[3]
            days = None if val == "unlimited" else int(val)
            
            exp = set_user_duration(target_uid, days)
            status_txt = get_expiry_status_text(exp)
            
            confirm_msg = send_msg(target_uid, f"⏳ <b>Your Snipely access has been activated/updated:</b> {status_txt}")
            
            def delayed_return():
              time.sleep(2)
              if confirm_msg:
                delete_single_msg(target_uid, confirm_msg)
              send_main_dashboard(target_uid)

            threading.Thread(target=delayed_return, daemon=True).start()
            send_admin_manage_single_user(chat_id, target_uid, message_id=mid)

          elif data.startswith("admin_toggle_role_") and is_admin(chat_id):
            target_uid = data.split("_")[3]
            res_role = toggle_admin_role(target_uid)
            if res_role == "admin":
              confirm_msg = send_msg(target_uid, "🎉 <b>Congratulations!</b> You have been promoted to <b>Admin</b> of Snipely.")
            else:
              confirm_msg = send_msg(target_uid, "ℹ️ Your admin privileges have ended.")
            
            def delayed_return():
              time.sleep(2)
              if confirm_msg:
                delete_single_msg(target_uid, confirm_msg)
              send_main_dashboard(target_uid)

            threading.Thread(target=delayed_return, daemon=True).start()
            send_admin_manage_single_user(chat_id, target_uid, message_id=mid)

          elif data.startswith("admin_remove_user_") and is_admin(chat_id):
            target_uid = data.split("_")[3]
            remove_user(target_uid)
            to_del = [k for k, m in active_monitors.items() if m.get("chat_id") == str(target_uid)]
            for k in to_del:
              active_monitors[k]["event"].set()
              active_monitors.pop(k, None)
            save_active_monitors()
            send_admin_users_list(chat_id, message_id=mid)

          elif data.startswith("admin_clear_monitors_") and is_admin(chat_id):
            target_uid = data.split("_")[3]
            to_del = [k for k, m in active_monitors.items() if m.get("chat_id") == str(target_uid)]
            for k in to_del:
              active_monitors[k]["event"].set()
              active_monitors.pop(k, None)
            save_active_monitors()
            send_admin_manage_single_user(chat_id, target_uid, message_id=mid)

          elif data == "admin_broadcast_msg" and is_admin(chat_id):
            user_states[chat_id] = {"stage": "admin_waiting_broadcast", "menu_mid": mid}
            kb = {"inline_keyboard": [[{"text": "❌ Cancel", "callback_data": "admin_panel"}]]}
            send_or_edit_msg(chat_id, "📢 <b>Type the message you want to send to ALL users:</b>", reply_markup=kb, message_id=mid)

          elif data == "admin_do_kill" and is_admin(chat_id):
            shutdown_bot(chat_id)

          # === MONITOR CALLBACKS ===
          elif data.startswith("open_edit_"):
            target_lid = int(data.split("_")[2])
            send_monitor_edit_panel(chat_id, target_lid, message_id=mid)

          elif data.startswith("quick_price_"):
            parts = data.split("_")
            target_lid = int(parts[2])
            diff = float(parts[3])
            key = f"{chat_id}_{target_lid}"

            if key in active_monitors:
              cur_p = active_monitors[key]["price"]
              new_p = max(1.0, round(cur_p + diff, 2))
              active_monitors[key]["price"] = new_p
              restart_monitor_with_new_settings(key)
              send_monitor_edit_panel(chat_id, target_lid, message_id=mid)

          elif data.startswith("set_price_"):
            target_lid = int(data.split("_")[2])
            user_states[chat_id] = {
                "stage": "editing_price",
                "target_lid": target_lid,
                "menu_mid": mid
            }
            kb = {
                "inline_keyboard": [[{
                    "text": "⬅️ Cancel",
                    "callback_data": f"open_edit_{target_lid}",
                }]]
            }
            send_or_edit_msg(
                chat_id,
                f"💰 Type the <b>new maximum amount in €</b> for Monitor #{target_lid}:",
                reply_markup=kb,
                message_id=mid,
            )

          elif data.startswith("set_inc_"):
            target_lid = int(data.split("_")[2])
            user_states[chat_id] = {
                "stage": "editing_inc",
                "target_lid": target_lid,
                "menu_mid": mid
            }
            kb = {
                "inline_keyboard": [
                    [{
                        "text": "⏩ No required words (All allowed)",
                        "callback_data": f"skip_inc_{target_lid}",
                    }],
                    [{
                        "text": "⬅️ Cancel",
                        "callback_data": f"open_edit_{target_lid}",
                    }],
                ]
            }
            send_or_edit_msg(
                chat_id,
                "🔍 Type the words that <b>must</b> appear (separated by commas):",
                reply_markup=kb,
                message_id=mid,
            )

          elif data.startswith("set_exc_"):
            target_lid = int(data.split("_")[2])
            user_states[chat_id] = {
                "stage": "editing_exc",
                "target_lid": target_lid,
                "menu_mid": mid
            }
            kb = {
                "inline_keyboard": [
                    [{
                        "text": "⏩ No forbidden words",
                        "callback_data": f"skip_exc_{target_lid}",
                    }],
                    [{
                        "text": "⬅️ Cancel",
                        "callback_data": f"open_edit_{target_lid}",
                    }],
                ]
            }
            send_or_edit_msg(
                chat_id,
                "🚫 Type the words that are <b>forbidden</b> (separated by commas):",
                reply_markup=kb,
                message_id=mid,
            )

          elif data.startswith("skip_inc_"):
            target_lid = int(data.split("_")[2])
            key = f"{chat_id}_{target_lid}"
            if key in active_monitors:
              active_monitors[key]["include_words"] = []
              restart_monitor_with_new_settings(key)
              user_states.pop(chat_id, None)
              send_monitor_edit_panel(chat_id, target_lid, message_id=mid)

          elif data.startswith("skip_exc_"):
            target_lid = int(data.split("_")[2])
            key = f"{chat_id}_{target_lid}"
            if key in active_monitors:
              active_monitors[key]["exclude_words"] = []
              restart_monitor_with_new_settings(key)
              user_states.pop(chat_id, None)
              send_monitor_edit_panel(chat_id, target_lid, message_id=mid)

          elif data.startswith("confirm_del_"):
            target_lid = int(data.split("_")[2])
            key = f"{chat_id}_{target_lid}"
            name = active_monitors.get(key, {}).get(
                "name", "this monitor"
            )
            kb = {
                "inline_keyboard": [
                    [{
                        "text": "✅ Yes, Delete Permanently",
                        "callback_data": f"do_del_{target_lid}",
                    }],
                    [{
                        "text": "❌ No, Cancel",
                        "callback_data": "menu_main",
                    }],
                ]
            }
            send_or_edit_msg(
                chat_id,
                f"❓ Are you sure you want to delete <b>#{target_lid}: {name}</b>?",
                reply_markup=kb,
                message_id=mid,
            )

          elif data.startswith("do_del_"):
            target_lid = int(data.split("_")[2])
            key = f"{chat_id}_{target_lid}"
            if key in active_monitors:
              name = active_monitors[key]["name"]
              active_monitors[key]["event"].set()
              active_monitors.pop(key, None)
              save_active_monitors()
              send_selection_list(chat_id, action_type="delete", message_id=mid)

          elif data == "wizard_skip_inc":
            if (
                chat_id in user_states
                and user_states[chat_id].get("stage") == "waiting_include"
            ):
              user_states[chat_id]["include_words"] = []
              user_states[chat_id]["stage"] = "waiting_exclude"
              kb = {
                  "inline_keyboard": [[{
                      "text": "⏩ No forbidden words",
                      "callback_data": "wizard_skip_exc",
                  }]]
              }
              delete_single_msg(chat_id, mid)
              send_msg(
                  chat_id,
                  "4️⃣ <b>Step 4 of 4:</b> What words are <b>FORBIDDEN</b>?\n\n<i>Type e.g.: <code>controller, defect</code> or click the button:</i>",
                  reply_markup=kb
              )

          elif data == "wizard_skip_exc":
            if (
                chat_id in user_states
                and user_states[chat_id].get("stage") == "waiting_exclude"
            ):
              st = user_states[chat_id]
              target_url = st["url"]
              price = st["price"]
              name = st["name"]
              include_words = st["include_words"]
              exclude_words = []

              user_states.pop(chat_id, None)
              local_id = get_next_available_local_id(chat_id)

              start_monitor_instance(
                  chat_id,
                  local_id,
                  name,
                  target_url,
                  price,
                  include_words,
                  exclude_words,
              )
              save_active_monitors()

              delete_single_msg(chat_id, mid)

              conf_m = send_msg(chat_id, "✅ <b>Monitor successfully saved!</b>")
              def delayed_return():
                time.sleep(2)
                if conf_m:
                  delete_single_msg(chat_id, conf_m)
                send_main_dashboard(chat_id)

              threading.Thread(target=delayed_return, daemon=True).start()
          continue

        msg = update.get("message", {})
        chat_id = str(msg.get("chat", {}).get("id"))
        text = msg.get("text", "").strip()
        msg_id = msg.get("message_id")

        if msg_id:
          track_msg_id(chat_id, msg_id)

        if not text:
          continue

        state = user_states.get(chat_id, {})
        stage = state.get("stage")
        menu_mid = state.get("menu_mid")

        known_users = load_known_users()
        if chat_id not in known_users and str(chat_id) != str(SUPER_ADMIN_ID).strip():
          delete_single_msg(chat_id, msg_id)
          send_msg(chat_id, f"⛔ <b>No access to Snipely.</b>\n\nYour Telegram ID is: <code>{chat_id}</code>\nAsk the administrator to add this ID to the system.")
          continue

        udata = known_users.get(chat_id, {})
        if not udata.get("setup_done", True) and str(chat_id) != str(SUPER_ADMIN_ID).strip():
          if stage == "waiting_discord_id":
            discord_id_val = text.strip()
            if discord_id_val.isdigit() and 17 <= len(discord_id_val) <= 20:
              users_map = load_known_users()
              users_map[chat_id]["name"] = discord_id_val
              users_map[chat_id]["setup_done"] = True
              save_known_users(users_map)

              user_states.pop(chat_id, None)
              delete_single_msg(chat_id, msg_id)
              if menu_mid:
                delete_single_msg(chat_id, menu_mid)

              conf_m = send_msg(chat_id, f"✅ <b>Discord ID linked:</b> <code>{discord_id_val}</code>\nWelcome to Snipely!")
              
              def delayed_return():
                time.sleep(2)
                if conf_m:
                  delete_single_msg(chat_id, conf_m)
                send_main_dashboard(chat_id)

              threading.Thread(target=delayed_return, daemon=True).start()
              print(f"[LOG] New user linked: Discord ID {discord_id_val} (Telegram ID: {chat_id})")
              send_msg(SUPER_ADMIN_ID, f"🔔 <b>Customer Activated:</b>\n🆔 Discord ID: <b>{discord_id_val}</b>\n💬 Telegram ID: <code>{chat_id}</code>")
              continue
            else:
              delete_single_msg(chat_id, msg_id)
              send_msg(chat_id, "❌ <b>Invalid Discord ID.</b>\nA valid Discord ID consists of numbers only (17 to 20 characters, e.g. <code>123456789012345678</code>).\n\nPlease try again:")
              continue
          else:
            user_states[chat_id] = {"stage": "waiting_discord_id"}
            delete_single_msg(chat_id, msg_id)
            send_msg(chat_id, "🎮 <b>Welcome to Snipely!</b>\n\nPlease enter your unique <b>Discord ID</b> (17 to 20 digits):")
            continue

        cmd = text.lower()

        if cmd == "/kill" and is_admin(chat_id):
          delete_single_msg(chat_id, msg_id)
          print(f"[LOG] Admin {chat_id} shut down the bot via /kill.")
          shutdown_bot(chat_id)

        if cmd in ("/start", "/menu", "/help", "menu"):
          delete_single_msg(chat_id, msg_id)
          send_main_dashboard(chat_id)
          continue

        if cmd in ("/lijst", "lijst"):
          delete_single_msg(chat_id, msg_id)
          send_user_monitors_list(chat_id)
          continue

        if cmd.startswith("/clear") or cmd == "clear":
          clear_chat_messages(chat_id)
          send_main_dashboard(chat_id)
          continue

        if stage == "admin_waiting_new_user_id" and is_admin(chat_id):
          user_states.pop(chat_id, None)
          delete_single_msg(chat_id, msg_id)
          target_new_id = text.strip()

          if target_new_id.isdigit():
            register_user(target_new_id, name="Pending...", days=None, setup_done=False)
            print(f"[LOG] Admin {chat_id} added Telegram ID {target_new_id} (waiting for Discord ID).")
            send_admin_time_selector(chat_id, target_new_id, message_id=menu_mid)
          else:
            send_or_edit_msg(chat_id, "❌ Invalid ID. Enter numbers only:", message_id=menu_mid)
          continue

        if stage == "admin_editing_discord_id" and is_admin(chat_id):
          target_uid = state.get("target_uid")
          new_d_id = text.strip()
          user_states.pop(chat_id, None)
          delete_single_msg(chat_id, msg_id)

          if new_d_id.isdigit() and 17 <= len(new_d_id) <= 20:
            users_map = load_known_users()
            if target_uid in users_map:
              old_id = users_map[target_uid]["name"]
              users_map[target_uid]["name"] = new_d_id
              save_known_users(users_map)
              print(f"[LOG] Admin {chat_id} changed Discord ID for user {target_uid} from {old_id} to {new_d_id}.")
              
              conf_m = send_msg(target_uid, f"ℹ️ Your Discord ID has been updated by the admin to: <b>{new_d_id}</b>")
              def delayed_return():
                time.sleep(2)
                if conf_m:
                  delete_single_msg(target_uid, conf_m)
                send_main_dashboard(target_uid)
              threading.Thread(target=delayed_return, daemon=True).start()

              send_admin_manage_single_user(chat_id, target_uid, message_id=menu_mid)
            else:
              send_msg(chat_id, "❌ User not found.")
          else:
            send_msg(chat_id, "❌ Invalid Discord ID (must be 17-20 digits). Please try again via the menu.")
            send_admin_manage_single_user(chat_id, target_uid, message_id=menu_mid)
          continue

        if stage == "admin_editing_limit" and is_admin(chat_id):
          target_uid = state.get("target_uid")
          val_str = text.strip().lower()
          user_states.pop(chat_id, None)
          delete_single_msg(chat_id, msg_id)

          if val_str == "unlimited":
            set_user_max_monitors(target_uid, "unlimited")
            print(f"[LOG] Admin {chat_id} set monitor limit for user {target_uid} to Unlimited.")
            
            conf_m = send_msg(target_uid, "ℹ️ Your maximum monitor limit has been updated by the admin to: <b>Unlimited</b>")
            def delayed_return():
              time.sleep(2)
              if conf_m:
                delete_single_msg(target_uid, conf_m)
              send_main_dashboard(target_uid)
            threading.Thread(target=delayed_return, daemon=True).start()

            send_admin_manage_single_user(chat_id, target_uid, message_id=menu_mid)
          elif val_str.isdigit():
            limit_num = int(val_str)
            set_user_max_monitors(target_uid, limit_num)
            print(f"[LOG] Admin {chat_id} set monitor limit for user {target_uid} to {limit_num}.")
            
            conf_m = send_msg(target_uid, f"ℹ️ Your maximum monitor limit has been updated by the admin to: <b>{limit_num}</b>")
            def delayed_return():
              time.sleep(2)
              if conf_m:
                delete_single_msg(target_uid, conf_m)
              send_main_dashboard(target_uid)
            threading.Thread(target=delayed_return, daemon=True).start()

            send_admin_manage_single_user(chat_id, target_uid, message_id=menu_mid)
          else:
            send_msg(chat_id, "❌ Invalid input. Type a number or 'unlimited'. Please try again via the menu.")
            send_admin_manage_single_user(chat_id, target_uid, message_id=menu_mid)
          continue

        if stage == "admin_waiting_broadcast" and is_admin(chat_id):
          user_states.pop(chat_id, None)
          delete_single_msg(chat_id, msg_id)
          if menu_mid:
            delete_single_msg(chat_id, menu_mid)
          users = load_known_users()
          count = 0
          for u in users.keys():
            try:
              send_msg(u, f"📢 <b>Snipely Announcement:</b>\n\n{text}")
              count += 1
            except Exception:
              pass
          print(f"[LOG] Admin {chat_id} sent a broadcast to {count} users.")
          send_admin_panel(chat_id)
          continue

        elif stage == "editing_price":
          target_lid = state.get("target_lid")
          key = f"{chat_id}_{target_lid}"
          val = text.replace(",", ".")
          delete_single_msg(chat_id, msg_id)
          if key in active_monitors and val.replace(".", "", 1).isdigit():
            active_monitors[key]["price"] = float(val)
            restart_monitor_with_new_settings(key)
            user_states.pop(chat_id, None)
            
            conf_m = send_msg(chat_id, "✅ <b>Price successfully updated!</b>")
            def delayed_return():
              time.sleep(2)
              if conf_m:
                delete_single_msg(chat_id, conf_m)
              send_monitor_edit_panel(chat_id, target_lid, message_id=menu_mid)
            threading.Thread(target=delayed_return, daemon=True).start()
          else:
            send_or_edit_msg(chat_id, "❌ Enter a valid amount (e.g. <code>250</code>):", message_id=menu_mid)
          continue

        elif stage == "editing_inc":
          target_lid = state.get("target_lid")
          key = f"{chat_id}_{target_lid}"
          delete_single_msg(chat_id, msg_id)
          if key in active_monitors:
            new_inc = (
                []
                if text.lower() == "geen" or text.lower() == "none"
                else [
                    w.strip().lower()
                    for w in text.split(",")
                    if w.strip()
                ]
            )
            active_monitors[key]["include_words"] = new_inc
            restart_monitor_with_new_settings(key)
            user_states.pop(chat_id, None)
            
            conf_m = send_msg(chat_id, "✅ <b>Required words updated!</b>")
            def delayed_return():
              time.sleep(2)
              if conf_m:
                delete_single_msg(chat_id, conf_m)
              send_monitor_edit_panel(chat_id, target_lid, message_id=menu_mid)
            threading.Thread(target=delayed_return, daemon=True).start()
          continue

        elif stage == "editing_exc":
          target_lid = state.get("target_lid")
          key = f"{chat_id}_{target_lid}"
          delete_single_msg(chat_id, msg_id)
          if key in active_monitors:
            new_exc = (
                []
                if text.lower() == "geen" or text.lower() == "none"
                else [
                    w.strip().lower()
                    for w in text.split(",")
                    if w.strip()
                ]
            )
            active_monitors[key]["exclude_words"] = new_exc
            restart_monitor_with_new_settings(key)
            user_states.pop(chat_id, None)
            
            conf_m = send_msg(chat_id, "✅ <b>Forbidden words updated!</b>")
            def delayed_return():
              time.sleep(2)
              if conf_m:
                delete_single_msg(chat_id, conf_m)
              send_monitor_edit_panel(chat_id, target_lid, message_id=menu_mid)
            threading.Thread(target=delayed_return, daemon=True).start()
          continue

        if (
            "vinted." in text
            and ("http://" in text or "https://" in text)
            or stage == "waiting_url"
        ):
          delete_single_msg(chat_id, msg_id)
          if "vinted." in text:
            user_states[chat_id] = {
                "stage": "waiting_name",
                "url": text,
                "menu_mid": menu_mid
            }
            send_or_edit_msg(
                chat_id,
                "🔗 <b>Link saved!</b>\n\n1️⃣ <b>Step 1 of 4:</b> Give this search a <b>name</b> (e.g. <code>PS5 Slim</code>):",
                message_id=menu_mid
            )
          else:
            send_or_edit_msg(chat_id, "❌ Please send a valid Vinted link:", message_id=menu_mid)

        elif stage == "waiting_name":
          delete_single_msg(chat_id, msg_id)
          user_states[chat_id]["name"] = text.strip()
          user_states[chat_id]["stage"] = "waiting_price"
          send_or_edit_msg(
              chat_id,
              f"2️⃣ <b>Step 2 of 4:</b> What is the <b>maximum price in €</b> for <b>{text.strip()}</b>?",
              message_id=menu_mid
          )

        elif stage == "waiting_price":
          delete_single_msg(chat_id, msg_id)
          try:
            price = float(text.replace(",", "."))
            user_states[chat_id]["price"] = price
            user_states[chat_id]["stage"] = "waiting_include"
            kb = {
                "inline_keyboard": [[{
                    "text": "⏩ None (All allowed)",
                    "callback_data": "wizard_skip_inc",
                }]]
            }
            send_or_edit_msg(
                chat_id,
                "3️⃣ <b>Step 3 of 4:</b> What words <b>MUST</b> appear in the title?\n\n<i>Type e.g.: <code>disc, new</code> or click the button:</i>",
                reply_markup=kb,
                message_id=menu_mid
            )
          except ValueError:
            send_or_edit_msg(chat_id, "❌ Enter a valid amount:", message_id=menu_mid)

        elif stage == "waiting_include":
          delete_single_msg(chat_id, msg_id)
          include_words = (
              []
              if text.lower() == "geen" or text.lower() == "none"
              else [
                  w.strip().lower()
                  for w in text.split(",")
                  if w.strip()
              ]
          )
          user_states[chat_id]["include_words"] = include_words
          user_states[chat_id]["stage"] = "waiting_exclude"
          kb = {
              "inline_keyboard": [[{
                  "text": "⏩ No forbidden words",
                  "callback_data": "wizard_skip_exc",
              }]]
          }
          send_or_edit_msg(
              chat_id,
              "4️⃣ <b>Step 4 of 4:</b> What words are <b>FORBIDDEN</b>?\n\n<i>Type e.g.: <code>controller, defect</code> or click the button:</i>",
              reply_markup=kb,
              message_id=menu_mid
          )

        elif stage == "waiting_exclude":
          delete_single_msg(chat_id, msg_id)
          exclude_words = (
              []
              if text.lower() == "geen" or text.lower() == "none"
              else [
                  w.strip().lower()
                  for w in text.split(",")
                  if w.strip()
              ]
          )
          target_url = state["url"]
          price = state["price"]
          name = state["name"]
          include_words = state["include_words"]

          user_states.pop(chat_id, None)
          local_id = get_next_available_local_id(chat_id)

          start_monitor_instance(
              chat_id,
              local_id,
              name,
              target_url,
              price,
              include_words,
              exclude_words,
          )
          save_active_monitors()

          delete_single_msg(chat_id, menu_mid)

          conf_m = send_msg(chat_id, "✅ <b>Monitor successfully saved!</b>")
          def delayed_return():
            time.sleep(2)
            if conf_m:
              delete_single_msg(chat_id, conf_m)
            send_main_dashboard(chat_id)

          threading.Thread(target=delayed_return, daemon=True).start()

    except Exception as e:
      print(f"[-] Polling error: {e}")
      time.sleep(3)


if __name__ == "__main__":
  while True:
    try:
      listen()
    except KeyboardInterrupt:
      print("\n[!] Snipely manually stopped.")
      sys.exit(0)
    except Exception as err:
      print(f"\n[!] Restarting after error: {err}")
      time.sleep(3)