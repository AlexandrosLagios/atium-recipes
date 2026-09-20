"""Every sentence the bot sends, in each language it speaks.

ponytail: a dict beats gettext here. Two languages and one process need no
catalogue compilation, no locale directory, and no build step in the image.
"""

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

DEFAULT = "en"

# Each label is written in its own language, because the chooser is the one
# place a user who cannot read the current language still has to understand.
LANGUAGES = {"en": "English", "el": "Ελληνικά"}


def from_code(code: str) -> str:
    """The language for a Telegram client's own language_code, which arrives
    as "el", "en-GB" or nothing at all."""
    base = (code or "").split("-")[0].lower()
    return base if base in LANGUAGES else DEFAULT


def language_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(label, callback_data=f"lang:{code}") for code, label in LANGUAGES.items()]]
    )


TEXT = {
    "connect": {
        "en": "Connect your Notion account to save recipes there.",
        "el": "Σύνδεσε τον λογαριασμό σου στο Notion για να αποθηκεύονται εκεί οι συνταγές.",
    },
    "connect_button": {"en": "Connect Notion", "el": "Σύνδεση με το Notion"},
    "connected": {
        "en": "Connected to '{page}'. Which language should I write your recipes in?",
        "el": "Συνδέθηκε με «{page}». Σε ποια γλώσσα να γράφω τις συνταγές σου;",
    },
    "connect_failed": {
        "en": "Connecting to Notion failed. Send me a message to try again.",
        "el": "Η σύνδεση με το Notion απέτυχε. Στείλε μου ένα μήνυμα για να δοκιμάσεις ξανά.",
    },
    "no_shared_page": {
        "en": "I didn't see a shared page. Send me a message and try again, and share a page this time.",
        "el": "Δεν είδα κοινόχρηστη σελίδα. Στείλε μου ένα μήνυμα και δοκίμασε ξανά, μοιράζοντας αυτή τη φορά μια σελίδα.",
    },
    "language_prompt": {
        "en": "Which language should I write your recipes in?",
        "el": "Σε ποια γλώσσα να γράφω τις συνταγές σου;",
    },
    "language_set": {
        "en": "Language: {language}. Send me a recipe.",
        "el": "Γλώσσα: {language}. Στείλε μου μια συνταγή.",
    },
    "ready": {
        "en": "Send me a recipe link, an Instagram or TikTok post, a photo, a PDF, or pasted text.",
        "el": "Στείλε μου έναν σύνδεσμο συνταγής, μια ανάρτηση στο Instagram ή στο TikTok, μια φωτογραφία, ένα PDF, ή επικολλημένο κείμενο.",
    },
    "disconnected": {
        "en": "Disconnected. Send me a message to connect a Notion account again.",
        "el": "Αποσυνδέθηκες. Στείλε μου ένα μήνυμα για να συνδέσεις ξανά λογαριασμό Notion.",
    },
    "blocked": {
        "en": "I could not open that post. Send me a screenshot of it and I will read that instead.",
        "el": "Δεν μπόρεσα να ανοίξω αυτή την ανάρτηση. Στείλε μου ένα στιγμιότυπο οθόνης και θα διαβάσω αυτό.",
    },
    "no_recipe": {
        "en": "I could not find a recipe in that.",
        "el": "Δεν βρήκα συνταγή εκεί μέσα.",
    },
    "file_type": {
        "en": "I can read a PDF or an image file. Send one of those, a photo, or paste the text.",
        "el": "Μπορώ να διαβάσω PDF ή αρχείο εικόνας. Στείλε ένα από αυτά, μια φωτογραφία, ή επικόλλησε το κείμενο.",
    },
    "too_big": {
        "en": "That file is over 20 MB, which Telegram will not hand me. Send a smaller one.",
        "el": "Αυτό το αρχείο ξεπερνά τα 20 MB και το Telegram δεν μου το δίνει. Στείλε ένα μικρότερο.",
    },
    "error": {
        "en": "Something went wrong handling that. Try again, or send it a different way.",
        "el": "Κάτι πήγε στραβά. Δοκίμασε ξανά, ή στείλ' το με άλλον τρόπο.",
    },
    "expired": {
        "en": "I no longer have that preview. Share the recipe again.",
        "el": "Δεν έχω πια αυτή την προεπισκόπηση. Στείλε ξανά τη συνταγή.",
    },
    "no_patch": {
        "en": 'I could not apply that. Try naming the field, for example "servings is 2".',
        "el": "Δεν μπόρεσα να το εφαρμόσω. Δοκίμασε να πεις ποιο πεδίο αλλάζει, για παράδειγμα «οι μερίδες είναι 2».",
    },
    "no_change": {"en": "That changed nothing.", "el": "Αυτό δεν άλλαξε τίποτα."},
    "no_source_text": {
        "en": "That page has no saved source text. Tap Refetch link to read the site again.",
        "el": "Αυτή η σελίδα δεν έχει αποθηκευμένο κείμενο πηγής. Πάτα «Νέα ανάγνωση» για να διαβάσω ξανά τον ιστότοπο.",
    },
    "saved": {"en": "Saved: {url}", "el": "Αποθηκεύτηκε: {url}"},
    "already_saved": {"en": "Already saved: {url}", "el": "Ήδη αποθηκευμένη: {url}"},
    "reimported": {"en": "Reimported: {url}", "el": "Ξαναέγινε εισαγωγή: {url}"},
    "discarded": {"en": "Discarded.", "el": "Απορρίφθηκε."},
    "save_failed": {
        "en": "Saving failed. Tap Save to try again.",
        "el": "Η αποθήκευση απέτυχε. Πάτα «Αποθήκευση» για να δοκιμάσεις ξανά.",
    },
    "save_button": {"en": "Save", "el": "Αποθήκευση"},
    "merge_button": {"en": "Save and merge", "el": "Αποθήκευση και συγχώνευση"},
    "drop_button": {"en": "Discard", "el": "Απόρριψη"},
    "preview_facts": {
        "en": "{time_min} min | {servings} servings",
        "el": "{time_min} λεπτά | {servings} μερίδες",
    },
    "preview_keeps": {"en": " | keeps {days}d", "el": " | κρατάει {days} ημ."},
    "preview_ingredients": {"en": "Ingredients: {items}", "el": "Υλικά: {items}"},
    "preview_method": {"en": "Method: {steps} steps", "el": "Εκτέλεση: {steps} βήματα"},
    "preview_new": {"en": "New ingredient rows: {items}", "el": "Νέα υλικά: {items}"},
    "preview_near": {
        "en": '"{proposed}" looks like the existing "{resembles}".',
        "el": "«{proposed}» μοιάζει με το υπάρχον «{resembles}».",
    },
    "preview_corrections": {"en": "Corrections: {items}", "el": "Διορθώσεις: {items}"},
    "preview_reply": {
        "en": "Reply to this message to correct it.",
        "el": "Απάντησε σε αυτό το μήνυμα για να το διορθώσεις.",
    },
    "refetch_button": {"en": "Refetch link", "el": "Νέα ανάγνωση"},
    "stored_button": {"en": "Reuse saved text", "el": "Αποθηκευμένο κείμενο"},
    "keep_button": {"en": "Keep", "el": "Διατήρηση"},
    "reimport_prompt": {
        "en": (
            "Already saved: {url}\n\nReimport it? Refetch link reads the site again. "
            "Reuse saved text runs the extraction over the text already on the page. "
            "Both replace the page body, so anything you wrote there by hand goes."
        ),
        "el": (
            "Ήδη αποθηκευμένη: {url}\n\nΝα γίνει ξανά εισαγωγή; Η «Νέα ανάγνωση» διαβάζει ξανά "
            "τον ιστότοπο. Το «Αποθηκευμένο κείμενο» τρέχει την εξαγωγή πάνω στο κείμενο που "
            "υπάρχει ήδη στη σελίδα. Και τα δύο αντικαθιστούν το σώμα της σελίδας, οπότε ό,τι "
            "έγραψες εκεί με το χέρι χάνεται."
        ),
    },
    "reimport_corrections": {
        "en": "\n\nBoth also apply your corrections again: {corrections}",
        "el": "\n\nΚαι τα δύο εφαρμόζουν ξανά τις διορθώσεις σου: {corrections}",
    },
    "reimport_kept": {"en": "Left as it is: {url}", "el": "Έμεινε ως έχει: {url}"},
    "reimporting": {"en": "Reimporting {url}", "el": "Γίνεται ξανά εισαγωγή: {url}"},
    "page_expired": {
        "en": "This link expired. Open Telegram and send a message to connect again.",
        "el": "Ο σύνδεσμος έληξε. Άνοιξε το Telegram και στείλε ένα μήνυμα για να συνδεθείς ξανά.",
    },
    "page_connected": {"en": "Connected. Go back to Telegram.", "el": "Συνδέθηκε. Γύρνα στο Telegram."},
    "owner_only": {
        "en": "That command is for the bot owner only.",
        "el": "Αυτή η εντολή είναι μόνο για τον ιδιοκτήτη του bot.",
    },
    "bad_user_id": {
        "en": "Send a numeric Telegram user id, like /allow 6529645381.",
        "el": "Στείλε ένα αριθμητικό αναγνωριστικό χρήστη Telegram, για παράδειγμα /allow 6529645381.",
    },
    "already_allowed": {
        "en": "{user_id} is already allowed.",
        "el": "Το {user_id} έχει ήδη πρόσβαση.",
    },
    "allowed_now": {
        "en": "Allowed {user_id}. They can message the bot now.",
        "el": "Δόθηκε πρόσβαση στο {user_id}. Μπορεί τώρα να στείλει μήνυμα στο bot.",
    },
    "deny_owner": {
        "en": "That is the owner id. Refusing to lock you out.",
        "el": "Αυτό είναι το αναγνωριστικό του ιδιοκτήτη. Δεν το αφαιρώ, για να μη μείνεις έξω.",
    },
    "deny_env_id": {
        "en": "{user_id} comes from TELEGRAM_ALLOWED_USER_IDS. Remove it there and restart the bot.",
        "el": "Το {user_id} προέρχεται από το TELEGRAM_ALLOWED_USER_IDS. Αφαίρεσέ το εκεί και επανεκκίνησε το bot.",
    },
    "not_allowed": {
        "en": "{user_id} is not allowed.",
        "el": "Το {user_id} δεν έχει πρόσβαση.",
    },
    "denied": {
        "en": "Denied {user_id}.",
        "el": "Αφαιρέθηκε η πρόσβαση για το {user_id}.",
    },
    "allowed_list": {
        "en": "Allowed users:\n{lines}",
        "el": "Χρήστες με πρόσβαση:\n{lines}",
    },
    "page_failed": {
        "en": "Something went wrong. Check Telegram for what to do next.",
        "el": "Κάτι πήγε στραβά. Δες στο Telegram τι να κάνεις.",
    },
}


# Positional-only, so a placeholder may be called {language} or {key} without
# colliding with these two.
def t(language: str, key: str, /, **values) -> str:
    template = TEXT[key].get(language) or TEXT[key][DEFAULT]
    return template.format(**values) if values else template
