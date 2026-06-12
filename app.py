"""
Xenergies Book Recommendation Chatbot API
GPT-4o-mini backend with WooCommerce order lookup (function calling)
"""
import os, json, ssl, urllib.request
from base64 import b64encode
from flask import Flask, request, jsonify, send_from_directory, redirect
from flask_cors import CORS

KB_PATH = os.path.join(os.path.dirname(__file__), "knowledge_base.json")
with open(KB_PATH, encoding="utf-8") as f:
    KB = json.load(f)

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_URL = "https://api.openai.com/v1/chat/completions"

WC_URL = os.getenv("WOOCOMMERCE_URL", "https://www.xenergies.com")
WC_KEY = os.getenv("WOOCOMMERCE_CONSUMER_KEY", "")
WC_SECRET = os.getenv("WOOCOMMERCE_CONSUMER_SECRET", "")

SUPPORT_URL = "https://xenergies.com/my-account-2/support-ticket/?wpsc-section=ticket-list"

SSL_CTX = ssl.create_default_context()
SSL_CTX.check_hostname = False
SSL_CTX.verify_mode = ssl.CERT_NONE

def books_context():
    lines = []
    for b in KB["books"]:
        price = b["price"]
        try:
            price = f"{float(price):.2f}"
        except Exception:
            pass
        lines.append(
            f"- {b['name']} (EUR {price})\n"
            f"  Description: {b['description'][:400]}\n"
            f"  URL: {b['url']}"
        )
    return "\n\n".join(lines)

SYSTEM_PROMPT = f"""Tu es un conseiller pour Xenergies, une boutique en ligne française spécialisée en huiles essentielles doTERRA et bien-être.

Tu peux aider les clients sur deux sujets :

1. LIVRES & LIVRETS — recommander le bon livre parmi notre catalogue
2. COMMANDES — vérifier le statut d'une commande en temps réel

CATALOGUE LIVRES :
{books_context()}

RÈGLES LIVRES :
- Recommande 1 à 2 livres maximum, les plus pertinents
- Inclus toujours le prix et le lien : [Voir le livre](url)
- Ne recommande pas Oracle (jeu de cartes) pour des besoins pratiques d'aromathérapie

RÈGLES COMMANDES :
- Si un client demande le statut ou le suivi de sa commande, demande son numéro de commande ET son adresse email
- Utilise l'outil get_order_status pour récupérer les informations
- Ne divulgue JAMAIS les informations d'une commande sans vérification email
- Si le champ "tracking" contient des données, présente le numéro de suivi et le lien cliquable si disponible
- Si aucun numéro de suivi n'est disponible, dis-le clairement et propose d'ouvrir un ticket support

STATUTS WOOCOMMERCE (traduis en français) :
- pending → En attente de paiement
- processing → En cours de traitement
- on-hold → En attente
- completed → Livré
- cancelled → Annulé
- refunded → Remboursé
- failed → Échec de paiement

SUPPORT :
- Si le problème est complexe ou si le client veut ouvrir un ticket, redirige vers :
  [Ouvrir un ticket support]({SUPPORT_URL})

RÈGLES GÉNÉRALES :
- Réponds toujours en français
- Sois chaleureux, concis, expert
- Si tu ne sais pas, dis-le honnêtement et suggère de contacter Xenergies
"""

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_order_status",
            "description": "Récupère le statut d'une commande WooCommerce après vérification de l'email client.",
            "parameters": {
                "type": "object",
                "properties": {
                    "order_id": {
                        "type": "string",
                        "description": "Le numéro de commande (ex: 1042)"
                    },
                    "email": {
                        "type": "string",
                        "description": "L'adresse email du client pour vérification"
                    }
                },
                "required": ["order_id", "email"]
            }
        }
    }
]

def wc_get_order(order_id, email):
    auth = b64encode(f"{WC_KEY}:{WC_SECRET}".encode()).decode()
    url = f"{WC_URL}/wp-json/wc/v3/orders/{order_id}"
    req = urllib.request.Request(url, headers={"Authorization": f"Basic {auth}"})
    try:
        with urllib.request.urlopen(req, context=SSL_CTX, timeout=10) as r:
            order = json.loads(r.read())
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return {"error": f"Commande #{order_id} introuvable."}
        return {"error": f"Erreur WooCommerce: {e.code}"}

    billing_email = order.get("billing", {}).get("email", "")
    if billing_email.lower() != email.lower():
        return {"error": "L'email ne correspond pas à cette commande. Veuillez vérifier."}

    items = [f"{i['name']} x{i['quantity']}" for i in order.get("line_items", [])]
    shipping = order.get("shipping_lines", [{}])[0].get("method_title", "")

    # Extract tracking from meta_data (WooCommerce Shipment Tracking / AST plugin)
    tracking = []
    for m in order.get("meta_data", []):
        key = m.get("key", "")
        val = m.get("value")
        if "tracking" in key.lower() and val:
            if isinstance(val, list):
                for t in val:
                    if isinstance(t, dict):
                        entry = {}
                        if t.get("tracking_number"):
                            entry["numero"] = t["tracking_number"]
                        if t.get("tracking_provider") or t.get("custom_tracking_provider"):
                            entry["transporteur"] = t.get("tracking_provider") or t.get("custom_tracking_provider")
                        if t.get("tracking_link") or t.get("custom_tracking_link"):
                            entry["lien"] = t.get("tracking_link") or t.get("custom_tracking_link")
                        if entry:
                            tracking.append(entry)
            elif isinstance(val, dict):
                entry = {}
                if val.get("tracking_number"):
                    entry["numero"] = val["tracking_number"]
                if val.get("tracking_provider"):
                    entry["transporteur"] = val["tracking_provider"]
                if val.get("tracking_link"):
                    entry["lien"] = val["tracking_link"]
                if entry:
                    tracking.append(entry)
            elif isinstance(val, str) and val.strip():
                tracking.append({"numero": val})

    return {
        "order_number": order.get("number"),
        "status": order.get("status"),
        "date_created": order.get("date_created", "")[:10],
        "total": f"{order.get('total', '?')} {order.get('currency', 'EUR')}",
        "items": items,
        "shipping_method": shipping,
        "customer_name": f"{order.get('billing', {}).get('first_name', '')} {order.get('billing', {}).get('last_name', '')}".strip(),
        "tracking": tracking if tracking else None,
    }

def openai_chat(message, history):
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    for h in history:
        role = "assistant" if h["role"] == "model" else "user"
        messages.append({"role": role, "content": h["parts"][0]})
    messages.append({"role": "user", "content": message})

    def call_openai(msgs, use_tools=True):
        body = {
            "model": "gpt-4o-mini",
            "messages": msgs,
            "max_tokens": 512,
            "temperature": 0.7
        }
        if use_tools:
            body["tools"] = TOOLS
            body["tool_choice"] = "auto"

        payload = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            OPENAI_URL,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {OPENAI_API_KEY}"
            },
            method="POST"
        )
        with urllib.request.urlopen(req, context=SSL_CTX, timeout=30) as r:
            return json.loads(r.read())

    # First call — may trigger tool use
    response = call_openai(messages)
    choice = response["choices"][0]

    if choice["finish_reason"] == "tool_calls":
        tool_call = choice["message"]["tool_calls"][0]
        fn_args = json.loads(tool_call["function"]["arguments"])
        order_data = wc_get_order(fn_args["order_id"], fn_args["email"])

        # Append assistant tool call + tool result, then get final reply
        messages.append(choice["message"])
        messages.append({
            "role": "tool",
            "tool_call_id": tool_call["id"],
            "content": json.dumps(order_data, ensure_ascii=False)
        })
        response = call_openai(messages, use_tools=False)
        choice = response["choices"][0]

    return choice["message"]["content"]

app = Flask(__name__)
CORS(app, origins=["https://xenergies.com", "https://www.xenergies.com", "http://localhost:*"])

@app.route("/")
def index():
    return redirect("/widget")

@app.route("/health")
def health():
    return jsonify({"status": "ok", "books": len(KB["books"])})

@app.route("/chat", methods=["POST"])
def chat():
    data = request.get_json()
    if not data or not data.get("message"):
        return jsonify({"error": "message required"}), 400

    message = data["message"].strip()[:500]
    history = data.get("history", [])

    try:
        reply = openai_chat(message, history)
        return jsonify({
            "reply": reply,
            "history": history + [
                {"role": "user", "parts": [message]},
                {"role": "model", "parts": [reply]},
            ]
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/widget")
def widget():
    return send_from_directory(os.path.dirname(__file__), "widget.html")

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5055))
    print(f"Book chatbot running on http://localhost:{port}")
    app.run(host="0.0.0.0", port=port, debug=False)
