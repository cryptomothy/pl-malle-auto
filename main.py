import pyautogui
import cv2
import numpy as np
import time
import threading
import sys
import ctypes
from ctypes import wintypes
import os
import keyboard
import json
import tkinter as tk

# Configuration
IMAGE_LEVEL = "./images/image_lvl_test.PNG"
IMAGE_JOIN = "./images/image_join_2.PNG"
IMAGE_OUT_FIGHT = "./images/image_out.PNG"  # Chemin vers l'image à détecter si hors combat
IMAGE_IN_FIGHT = "./images/image_endturn.PNG"  # Chemin vers l'image à détecter en combat
OUT_FIGHT_REGION = (655, 961, 31, 29)  # Zone à surveiller (x, y, largeur, hauteur)
IN_FIGHT_REGION = (1229, 959, 167, 42)  # Zone de l'image de combat
LVL_REGION = (881, 718, 158, 20) # (x, y, largeur, hauteur)
LVL_REGION_FALLBACK = (882, 560, 156, 20) # (x, y, largeur, hauteur)
JOIN_REGION = (8, 492, 36, 39) # (x, y, largeur, hauteur)

# Secours après un retry (combat < FIGHT_MIN_DURATION) quand le jeu n'affiche pas la popup join :
# on ouvre la fenêtre Spectateur et on double-clique sur l'icône de groupe pour rejoindre
IMAGE_GROUP_ICON = "./images/image_group_icon.PNG"  # Icône indiquant le combat de notre groupe
SPECTATOR_LIST_REGION = (660, 250, 600, 400)  # Zone de la fenêtre Spectateur où chercher l'icône de groupe
SPECTATOR_FIRST_DELAY = 10  # Secondes après le retry sans popup join avant le premier essai
SPECTATOR_CHECK_INTERVAL = 10  # Secondes entre deux essais suivants
GROUP_ICON_CONFIDENCE = 0.8
SPECTATOR_DRY_RUN = False  # True = ouvre la fenêtre et journalise la détection sans double-cliquer l'icône

CONFIDENCE = 0.7  # Seuil de confiance pour la détection (0.0 à 1.0)
LVL_CONFIDENCE = 0.6  # Seuil de confiance pour le level up (plus strict pour éviter les faux positifs)
CHECK_INTERVAL = 3  # Intervalle en secondes entre chaque vérification
TIMEOUT_THRESHOLD = 30  # Seuil en secondes pour détecter un blocage
DEBUG_MODE = False  # Active les messages de debug détaillés

RESTART_CLICK_POSITIONS = [
    (1898, 10),
    (293, 450),
    (1034, 568)
]

# Coordonnées où cliquer quand l'image est détectée
RESTART_DUNGEON_CLICK_POSITIONS = [
    (874, 371),  
    (1111, 418), 
    (582, 329), 
    (836, 391),  
    (1030, 441), 
    (1030, 441)
]

JOIN_CLICK_POSITIONS = [
    (170, 535)  # Premier clic pour rejoindre
]

SPECTATOR_BUTTON_CLICK = [
    (960, 20)
] # Bouton Spectateur en haut
# Variables globales pour le monitoring
last_check_time = time.time()
is_running = True
check_counter = 0
start_time = time.time()  # Temps de départ pour le restart
watchdog_thread_ref = None  # Référence au thread watchdog
fight_counter = 1  # Compteur de fights
counter_window = None  # Référence à la fenêtre du compteur
fight_start_time = 0  # Timer de début de séquence de combat
FIGHT_MIN_DURATION = 70  # Durée minimum en secondes avant de considérer un restart dungeon
RESTART_MIN_FIGHT_SECONDS = 45  # Le restart client n'est lancé que si le combat tient au moins ce délai

# Délai entre chaque clic (en secondes)
CLICK_DELAY = 1.5  # 2.5 secondes
CLICK_DELAY_SMALLER = 2  # 2 secondes

# Configuration surchargeable via config.json (généré par setup_gui.py)
CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
REGION_KEYS = [
    "OUT_FIGHT_REGION",
    "IN_FIGHT_REGION",
    "LVL_REGION",
    "LVL_REGION_FALLBACK",
    "JOIN_REGION",
    "SPECTATOR_LIST_REGION",
]
CLICK_KEYS = [
    "RESTART_CLICK_POSITIONS",
    "RESTART_DUNGEON_CLICK_POSITIONS",
    "JOIN_CLICK_POSITIONS",
    "SPECTATOR_BUTTON_CLICK",
]
DEFAULT_CONFIG = {
    "regions": {key: tuple(globals()[key]) for key in REGION_KEYS},
    "clicks": {key: [tuple(p) for p in globals()[key]] for key in CLICK_KEYS},
}

def load_config():
    """Charge config.json (si présent) et surcharge les régions / positions de clic"""
    if not os.path.exists(CONFIG_FILE):
        return
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        print(f"❌ Erreur lors de la lecture de {CONFIG_FILE}: {e} (valeurs par défaut utilisées)")
        return
    g = globals()
    for key, value in data.get("regions", {}).items():
        if key in REGION_KEYS:
            g[key] = tuple(value)
    for key, value in data.get("clicks", {}).items():
        if key in CLICK_KEYS:
            g[key] = [tuple(p) for p in value]

load_config()

def debug_print(message):
    """Affiche un message de debug avec timestamp si le mode debug est activé"""
    if DEBUG_MODE:
        timestamp = time.strftime('%H:%M:%S')
        print(f"[{timestamp}] DEBUG: {message}")

class CounterOverlay:
    """Classe pour gérer l'overlay du compteur"""
    def __init__(self):
        self.root = tk.Tk()
        self.root.withdraw()  # Cacher la fenêtre principale
        
        # Créer une fenêtre toplevel pour le compteur
        self.window = tk.Toplevel(self.root)
        self.window.overrideredirect(True)  # Enlever les bordures
        self.window.attributes('-topmost', True)  # Toujours au-dessus
        self.window.configure(bg='black')  # Fond noir
        
        # Positionner en haut à droite
        screen_width = self.window.winfo_screenwidth()
        window_width = 200
        window_height = 80
        x = screen_width - window_width - 10
        y = 100
        self.window.geometry(f'{window_width}x{window_height}+{x}+{y}')
        
        # Label pour le compteur
        self.counter_label = tk.Label(
            self.window,
            text="1",
            font=('Arial', 24, 'bold'),
            fg='lime',
            bg='black'
        )
        self.counter_label.pack(expand=True)
        
        # Afficher la fenêtre
        self.window.deiconify()
    
    def update_counter(self, value):
        """Met à jour la valeur du compteur"""
        self.counter_label.config(text=str(value))
    
    def update(self):
        """Met à jour la fenêtre tkinter"""
        try:
            self.root.update()
        except:
            pass

def create_counter_overlay():
    """Crée l'overlay du compteur dans un thread séparé"""
    global counter_window
    counter_window = CounterOverlay()
    debug_print("Overlay du compteur créé")

def update_fight_counter():
    """Incrémente le compteur de fights et met à jour l'affichage"""
    global fight_counter, counter_window
    fight_counter += 1
    print(f"⚔️ Fight #{fight_counter}")
    
    if counter_window:
        counter_window.update_counter(fight_counter)
        counter_window.update()

def watchdog_thread():
    """Thread de surveillance qui vérifie si le script principal fonctionne"""
    global last_check_time, is_running
    
    while is_running:
        current_time = time.time()
        time_since_last_check = current_time - last_check_time
        
        if time_since_last_check > TIMEOUT_THRESHOLD:
            print(f"\n⚠️  ATTENTION: Aucune activité détectée depuis {time_since_last_check:.1f}s")
            print("Le script semble bloqué. Tentative de redémarrage automatique...")
            # Reset du timer
            last_check_time = current_time
            
        time.sleep(5)  # Vérification toutes les 5 secondes

def load_template_image(image_path):
    """Charge l'image template à détecter"""
    try:
        debug_print(f"Tentative de chargement de l'image: {image_path}")
        
        # Vérifier si le fichier existe
        if not os.path.exists(image_path):
            print(f"❌ Le fichier {image_path} n'existe pas")
            return None
        
        template = cv2.imread(image_path, cv2.IMREAD_COLOR)
        if template is None:
            raise ValueError(f"Impossible de charger l'image: {image_path}")
        
        # Afficher les dimensions de l'image chargée
        height, width = template.shape[:2]
        debug_print(f"✅ Image {image_path} chargée avec succès - Dimensions: {width}x{height}")
        
        return template
    except Exception as e:
        print(f"❌ Erreur lors du chargement de l'image {image_path}: {e}")
        return None
    
def safe_screenshot_region(region, max_retries=3):
    """Capture d'écran avec gestion des erreurs et retry automatique"""
    for attempt in range(max_retries):
        try:
            debug_print(f"Tentative de capture d'écran {attempt + 1}/{max_retries}")
            # Petit délai pour éviter les problèmes de timing
            time.sleep(0.1)
            screenshot = pyautogui.screenshot(region=region)
            screenshot_cv = cv2.cvtColor(np.array(screenshot), cv2.COLOR_RGB2BGR)
            debug_print("Capture d'écran réussie")
            return screenshot_cv
        except Exception as e:
            debug_print(f"Erreur capture tentative {attempt + 1}: {e}")
            if attempt < max_retries - 1:
                time.sleep(0.5)  # Attente avant retry
            else:
                print(f"Erreur persistante lors de la capture d'écran: {e}")
                return None

def capture_screen_region(region):
    """Capture une région spécifique de l'écran avec amélioration de stabilité"""
    return safe_screenshot_region(region)

def detect_image_in_region(template, region, image_name="", confidence_threshold=None, verbose=False):
    """Détecte si l'image template est présente dans la région spécifiée.
    verbose=True affiche la raison d'un échec"""
    global last_check_time, check_counter
    fail_print = print if verbose else debug_print
    
    # Utiliser le seuil de confiance personnalisé ou le seuil par défaut
    if confidence_threshold is None:
        confidence_threshold = CONFIDENCE
    
    # Mise à jour du timestamp
    last_check_time = time.time()
    if image_name == "":  # Ne compter que pour l'image hors combat
        check_counter += 1
    
    debug_print(f"Début détection {image_name} #{check_counter if image_name == '' else 'N/A'} (seuil: {confidence_threshold:.2f})")
    
    screenshot = capture_screen_region(region)
    if screenshot is None or template is None:
        debug_print(f"Échec capture ou template manquant pour {image_name}")
        return False
    
    try:
        # Vérification de la luminosité moyenne de la zone
        gray_screenshot = cv2.cvtColor(screenshot, cv2.COLOR_BGR2GRAY)
        avg_brightness = np.mean(gray_screenshot)
        
        # Si la zone est trop sombre (noire), ne pas détecter
        if avg_brightness < 30:  # Seuil pour considérer la zone comme noire
            fail_print(f"❌ Zone trop sombre pour {image_name} (luminosité: {avg_brightness:.1f})")
            return False
        
        # Détection par template matching avec plusieurs méthodes
        debug_print(f"Exécution du template matching pour {image_name}...")
        
        # Méthode 1: TM_CCOEFF_NORMED (la plus fiable)
        result1 = cv2.matchTemplate(screenshot, template, cv2.TM_CCOEFF_NORMED)
        min_val1, max_val1, min_loc1, max_loc1 = cv2.minMaxLoc(result1)
        
        # Méthode 2: TM_SQDIFF_NORMED (plus stricte pour les zones noires)
        result2 = cv2.matchTemplate(screenshot, template, cv2.TM_SQDIFF_NORMED)
        min_val2, max_val2, min_loc2, max_loc2 = cv2.minMaxLoc(result2)
        
        # Pour TM_SQDIFF_NORMED, on cherche la valeur minimale (plus proche de 0 = meilleure correspondance)
        # Convertir en score de confiance (1 - min_val2)
        confidence2 = 1 - min_val2
        
        # Utiliser seulement les deux méthodes les plus fiables
        final_confidence = (max_val1 + confidence2) / 2
        
        debug_print(f"TM_CCOEFF_NORMED: {max_val1:.3f}, TM_SQDIFF_NORMED: {confidence2:.3f}, Final: {final_confidence:.3f}")
        
        # Vérification supplémentaire : les deux méthodes doivent être au-dessus d'un seuil minimum
        min_individual_threshold = confidence_threshold * 0.8  # 80% du seuil principal
        if max_val1 < min_individual_threshold or confidence2 < min_individual_threshold:
            fail_print(f"❌ {image_name} : une des méthodes sous le seuil minimum ({min_individual_threshold:.3f}) - "
                       f"TM_CCOEFF_NORMED: {max_val1:.3f}, TM_SQDIFF_NORMED: {confidence2:.3f}, luminosité: {avg_brightness:.1f}")
            return False
        
        # Vérification si la correspondance dépasse le seuil de confiance
        if final_confidence >= confidence_threshold:
            print(f"✅ {image_name} détectée avec confiance: {final_confidence:.3f} (luminosité: {avg_brightness:.1f})")
            return True
        else:
            fail_print(f"❌ {image_name} non détectée (confiance: {final_confidence:.3f}, luminosité: {avg_brightness:.1f})")
            return False
            
    except Exception as e:
        print(f"Erreur lors de la détection {image_name}: {e}")
        debug_print(f"Stack trace: {sys.exc_info()}")
        return False

def perform_level_up_click():
    """Effectue l'appui sur Escape pour le level up"""
    print("🎯 Level up détecté ! Appui sur Escape...")
        
    try:
        print("Appui sur la touche Escape (méthode 1: press)")
        keyboard.press_and_release('esc')
        time.sleep(1)
        
    except Exception as e:
        print(f"Erreur lors de l'appui sur Escape: {e}")
    
    print("✅ Appui sur Escape terminé.")

# SetCursorPos met GetCursorPos à jour tout de suite, mais le clic injecté
# ensuite (bouton seul, sans position) voyage dans la file d'entrée, qui peut
# encore viser l'ancien point. Windows traite alors le clic, puis seulement le
# déplacement. C'est aléatoire, et visible surtout sur un grand saut : le 3e
# clic du restart donjon (~500 px, de 1111,418 vers 582,329).
# Le déplacement absolu et l'appui partent donc dans le même SendInput.
# ABSOLUTE est obligatoire : sans lui, Windows lit dx/dy comme un déplacement
# relatif (c'est le saut parasite de pyautogui.click). L'appui et le relâchement
# restent deux événements séparés, tenus assez longtemps pour qu'une frame du
# jeu ne puisse pas les rater.
_MOUSEEVENTF_MOVE = 0x0001
_MOUSEEVENTF_LEFTDOWN = 0x0002
_MOUSEEVENTF_LEFTUP = 0x0004
_MOUSEEVENTF_ABSOLUTE = 0x8000
_MOUSEEVENTF_VIRTUALDESK = 0x4000
_INPUT_MOUSE = 0
_SM_XVIRTUALSCREEN = 76
_SM_YVIRTUALSCREEN = 77
_SM_CXVIRTUALSCREEN = 78
_SM_CYVIRTUALSCREEN = 79

class _MOUSEINPUT(ctypes.Structure):
    _fields_ = (
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_void_p),
    )

class _MOUSE_INPUT(ctypes.Structure):
    _fields_ = (
        ("type", wintypes.DWORD),
        ("mi", _MOUSEINPUT),
    )

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_send_input = _user32.SendInput
_send_input.argtypes = (wintypes.UINT, ctypes.POINTER(_MOUSE_INPUT), ctypes.c_int)
_send_input.restype = wintypes.UINT
_set_cursor_pos = _user32.SetCursorPos
_set_cursor_pos.argtypes = (ctypes.c_int, ctypes.c_int)
_set_cursor_pos.restype = wintypes.BOOL
_get_cursor_pos = _user32.GetCursorPos
_get_cursor_pos.argtypes = (ctypes.POINTER(wintypes.POINT),)
_get_cursor_pos.restype = wintypes.BOOL
_mouse_event = _user32.mouse_event
_mouse_event.argtypes = (
    wintypes.DWORD,
    wintypes.DWORD,
    wintypes.DWORD,
    wintypes.DWORD,
    ctypes.c_ulonglong,
)
_mouse_event.restype = None
_get_system_metrics = _user32.GetSystemMetrics
_get_system_metrics.argtypes = (ctypes.c_int,)
_get_system_metrics.restype = ctypes.c_int

def _cursor_pos():
    point = wintypes.POINT()
    if not _get_cursor_pos(ctypes.byref(point)):
        raise OSError("GetCursorPos a échoué")
    return point.x, point.y

def _cursor_is_at(x, y, tolerance=2):
    cx, cy = _cursor_pos()
    return abs(cx - x) <= tolerance and abs(cy - y) <= tolerance

def _absolute_coords(x, y):
    """Coordonnées écran normalisées sur 0-65535, bureau virtuel compris."""
    left = _get_system_metrics(_SM_XVIRTUALSCREEN)
    top = _get_system_metrics(_SM_YVIRTUALSCREEN)
    width = _get_system_metrics(_SM_CXVIRTUALSCREEN)
    height = _get_system_metrics(_SM_CYVIRTUALSCREEN)
    if width <= 1 or height <= 1:
        raise OSError(f"Bureau virtuel invalide ({width}x{height})")
    ax = int(round((x - left) * 65535 / (width - 1)))
    ay = int(round((y - top) * 65535 / (height - 1)))
    return max(0, min(65535, ax)), max(0, min(65535, ay))

def _mouse_input(dx, dy, flags):
    return _MOUSE_INPUT(type=_INPUT_MOUSE, mi=_MOUSEINPUT(dx, dy, 0, flags, 0, None))

def _send_many(events):
    """Insère plusieurs événements d'un bloc, dans l'ordre, sans intercalage."""
    batch = (_MOUSE_INPUT * len(events))(*events)
    sent = _send_input(len(events), batch, ctypes.sizeof(_MOUSE_INPUT))
    if sent == len(events):
        return
    if sent == 0:
        err = ctypes.get_last_error()
        for event in events:
            _mouse_event(event.mi.dwFlags, event.mi.dx, event.mi.dy, 0, 0)
        debug_print(f"SendInput refusé (erreur {err}), repli sur mouse_event")
        return
    raise OSError(f"SendInput incomplet ({sent}/{len(events)})")

def _move_cursor(x, y):
    """Déplace le curseur par un vrai événement absolu, puis vérifie qu'il y est."""
    ax, ay = _absolute_coords(x, y)
    flags = _MOUSEEVENTF_MOVE | _MOUSEEVENTF_ABSOLUTE | _MOUSEEVENTF_VIRTUALDESK
    last = None
    for _ in range(8):
        _send_many([_mouse_input(ax, ay, flags)])
        time.sleep(0.02)
        if _cursor_is_at(x, y):
            # Deux lectures : écarter un GetCursorPos qui n'a pas encore suivi.
            time.sleep(0.02)
            if _cursor_is_at(x, y):
                return ax, ay
        last = _cursor_pos()
    _set_cursor_pos(x, y)
    time.sleep(0.02)
    _send_many([_mouse_input(ax, ay, flags)])
    time.sleep(0.02)
    if _cursor_is_at(x, y):
        return ax, ay
    raise OSError(f"Le curseur n'a pas atteint ({x}, {y}), position actuelle {last}")

def click_at(x, y, clicks=1, settle=0.1, hold=0.1):
    """Attend que le curseur soit vraiment arrivé, puis clique à cette position.
    Le move et l'appui sont un seul envoi : le clic ne peut plus partir avant
    le déplacement. L'appui et le relâchement restent séparés."""
    x, y = int(x), int(y)
    ax = ay = None
    for _ in range(4):
        ax, ay = _move_cursor(x, y)
        time.sleep(settle)
        if _cursor_is_at(x, y):
            break
    else:
        raise OSError(f"Le curseur a quitté ({x}, {y}) avant le clic, position {_cursor_pos()}")

    move = _MOUSEEVENTF_MOVE | _MOUSEEVENTF_ABSOLUTE | _MOUSEEVENTF_VIRTUALDESK
    for i in range(clicks):
        # Relu juste avant l'envoi : si le curseur a bougé pendant l'attente,
        # on le repose et on recommence le délai, sans cliquer au passage.
        if not _cursor_is_at(x, y):
            ax, ay = _move_cursor(x, y)
            time.sleep(settle)
            if not _cursor_is_at(x, y):
                raise OSError(f"Le curseur n'est pas stable en ({x}, {y}), position {_cursor_pos()}")
        _send_many([
            _mouse_input(ax, ay, move),
            _mouse_input(ax, ay, move | _MOUSEEVENTF_LEFTDOWN),
        ])
        time.sleep(hold)
        _send_many([_mouse_input(ax, ay, move | _MOUSEEVENTF_LEFTUP)])
        if i + 1 < clicks:
            time.sleep(0.1)

def perform_restart_dungeon():
    print("🎯 Image hors combat détectée ! Exécution des clics...")
    
    for i, (x, y) in enumerate(RESTART_DUNGEON_CLICK_POSITIONS, 1):
        try:
            print(f"Clic {i} à la position ({x}, {y})")
            click_at(x, y)
            # Utiliser un délai plus court pour les positions 0, 1, 4 et 5
            delay = CLICK_DELAY_SMALLER if i in [0, 1, 4, 5] else CLICK_DELAY
            time.sleep(delay)
        except Exception as e:
            print(f"Erreur lors du clic {i}: {e}")
    
    print("✅ Séquence de clics terminée.")

def perform_join_click():
    """Effectue le clic pour rejoindre"""
    print("🎯 Image join détectée ! Exécution du clic...")
    
    for i, (x, y) in enumerate(JOIN_CLICK_POSITIONS, 1):
        try:
            print(f"Clic join {i} à la position ({x}, {y})")
            click_at(x, y)
            time.sleep(CLICK_DELAY)
        except Exception as e:
            print(f"Erreur lors du clic join {i}: {e}")
    
    print("✅ Clic join terminé.")

def find_best_match(template, region, confidence_threshold):
    """Cherche le template dans toute la région. Retourne (x, y, score) du centre en coordonnées écran, ou None"""
    screenshot = capture_screen_region(region)
    if screenshot is None or template is None:
        return None
    th, tw = template.shape[:2]
    if screenshot.shape[0] < th or screenshot.shape[1] < tw:
        print(f"❌ La zone de recherche {region} est plus petite que l'image ({tw}x{th})")
        return None

    result = cv2.matchTemplate(screenshot, template, cv2.TM_CCOEFF_NORMED)
    _, max_val, _, max_loc = cv2.minMaxLoc(result)
    if max_val < confidence_threshold:
        debug_print(f"Meilleure correspondance sous le seuil: {max_val:.3f}")
        return None
    return region[0] + max_loc[0] + tw // 2, region[1] + max_loc[1] + th // 2, max_val

def join_via_spectator(group_icon_template, open_window):
    """Cherche l'icône de groupe dans la fenêtre Spectateur et double-clique dessus pour rejoindre.
    open_window=False : la fenêtre a déjà été ouverte et reste ouverte (recliquer le bouton la fermerait).
    Retourne True si le combat a été rejoint."""
    if open_window:
        print("👀 Popup join absente, ouverture de la fenêtre Spectateur...")
        for x, y in SPECTATOR_BUTTON_CLICK:
            click_at(x, y)
        time.sleep(CLICK_DELAY_SMALLER)

    match = find_best_match(group_icon_template, SPECTATOR_LIST_REGION, GROUP_ICON_CONFIDENCE)
    if match is None:
        print("🔍 Icône de groupe absente de la fenêtre Spectateur, combat pas encore lancé")
        return False

    icon_x, icon_y, score = match
    print(f"🎯 Icône de groupe trouvée en ({icon_x}, {icon_y}) (confiance: {score:.3f})")

    if SPECTATOR_DRY_RUN:
        print(f"🧪 DRY RUN : aurait double-cliqué l'icône en ({icon_x}, {icon_y}) pour rejoindre")
        return False

    click_at(icon_x, icon_y, clicks=2)
    print("✅ Double-clic sur l'icône de groupe pour rejoindre")
    time.sleep(CLICK_DELAY)
    return True

def perform_restart_sequence():
    global start_time, is_running, watchdog_thread_ref
    
    print("\n🔄 Séquence de restart - 30 minutes écoulées !")
    print("🎯 Exécution de la séquence de clics de restart...")
    
    try:
        # Clic 1
        x1, y1 = RESTART_CLICK_POSITIONS[0]
        print(f"Clic restart 1 à la position ({x1}, {y1})")
        click_at(x1, y1)
        print("⏳ Attente de 3 secondes...")
        time.sleep(3)
        
        # Clic 2
        x2, y2 = RESTART_CLICK_POSITIONS[1]
        print(f"Clic restart 2 à la position ({x2}, {y2})")
        click_at(x2, y2)
        print("⏳ Attente de 12 secondes...")
        time.sleep(12)
        
        # Clic 3
        x3, y3 = RESTART_CLICK_POSITIONS[2]
        print(f"Clic restart 3 à la position ({x3}, {y3})")
        click_at(x3, y3)
        
    except Exception as e:
        print(f"❌ Erreur lors de la séquence de restart: {e}")
    
    print("✅ Séquence de restart terminée.")
    print("🔄 Arrêt propre du thread de surveillance...")
    
    # Arrêter le thread de surveillance proprement
    is_running = False
    
    # Attendre que le thread watchdog se termine (max 10 secondes)
    if watchdog_thread_ref and watchdog_thread_ref.is_alive():
        watchdog_thread_ref.join(timeout=10)
        if watchdog_thread_ref.is_alive():
            print("⚠️ Le thread watchdog n'a pas pu se terminer proprement")
        else:
            print("✅ Thread watchdog terminé proprement")
    
    print("🔄 Réinitialisation complète du script dans 2 secondes...")
    time.sleep(2)
    
    # Relancer le script Python depuis le début
    # os.execv() remplace COMPLÈTEMENT le processus actuel :
    # - Même PID, mais nouvelle image mémoire
    # - Tous les threads/variables/connexions sont terminés
    # - Le script redémarre comme si on l'avait lancé à nouveau
    print("🚀 Redémarrage du script...\n")
    os.execv(sys.executable, ['python'] + sys.argv)

def perform_fight_sequence(second_template, out_template):
    """Après le clic join, bloque jusqu'au vrai début du combat.

    L'image hors combat est encore à l'écran juste après le clic : on ne rend
    pas la main à la boucle principale tant que l'image de combat n'est pas
    visible ET que l'image hors combat a disparu. Sinon elle est redétectée
    immédiatement, avant que le combat ait commencé.
    """
    global start_time, fight_start_time

    fight_start_time = 0

    print("\n⚔️ Clic join effectué. Attente du début du combat...")
    print("🔍 L'image hors combat est ignorée tant que le combat n'a pas commencé.")

    while is_running:
        in_fight = detect_image_in_region(
            second_template,
            IN_FIGHT_REGION,
            "Image de combat"
        )

        if not in_fight:
            print("⏳ Début du combat non détecté, nouvelle vérification dans 3 secondes...")
            time.sleep(3)
            continue

        out_still_visible = detect_image_in_region(
            out_template,
            OUT_FIGHT_REGION,
            "Image hors combat (encore affichée)"
        )
        if out_still_visible:
            print("⏳ Image de combat vue, mais l'image hors combat est encore affichée. On attend le vrai début...")
            time.sleep(3)
            continue

        # Deuxième lecture : un raté ponctuel ne doit pas valider le début trop tôt
        time.sleep(1)
        if not detect_image_in_region(second_template, IN_FIGHT_REGION, "Image de combat"):
            print("⏳ Image de combat perdue, on continue d'attendre...")
            time.sleep(2)
            continue
        if detect_image_in_region(out_template, OUT_FIGHT_REGION, "Image hors combat (encore affichée)"):
            print("⏳ Image hors combat réapparue, le combat n'est pas lancé.")
            time.sleep(2)
            continue

        fight_start_time = time.time()
        update_fight_counter()
        print("🎉 Début du combat confirmé. L'image hors combat ne sera prise en compte qu'à la fin du combat.")
        break

    if not is_running:
        return

    # Vérifier le temps écoulé depuis le départ
    elapsed_time = time.time() - start_time
    elapsed_minutes = elapsed_time / 60
    
    print(f"⏱️ Temps écoulé : {elapsed_minutes:.1f} minutes")
    
    # Restart client seulement si le combat tient RESTART_MIN_FIGHT_SECONDS.
    # On ne dort pas d'un bloc : si l'image hors combat revient, le combat est
    # fini et la boucle principale doit pouvoir enchaîner le restart fight.
    if elapsed_minutes >= 30:
        print(f"⏳ 30 minutes écoulées. Restart si le combat tient encore {RESTART_MIN_FIGHT_SECONDS}s...")
        while is_running and (time.time() - fight_start_time) < RESTART_MIN_FIGHT_SECONDS:
            if detect_image_in_region(out_template, OUT_FIGHT_REGION, "Image hors combat"):
                elapsed = time.time() - fight_start_time
                print(f"⚔️ Combat terminé après {elapsed:.0f}s (< {RESTART_MIN_FIGHT_SECONDS}s). Restart sequence annulé, retour au restart fight.")
                return
            remaining = RESTART_MIN_FIGHT_SECONDS - (time.time() - fight_start_time)
            time.sleep(min(2, max(0, remaining)))
        if is_running:
            print("⏳ Combat toujours en cours, lancement de la séquence de restart...")
            perform_restart_sequence()
    

def test_level_up_detection(level_template):
    """Test de détection de l'image level up pour diagnostiquer les problèmes"""
    print("\n🔬 Test de détection de l'image level up...")
    print(f"Zone de test: {LVL_REGION}")
    
    # Capture d'écran de la zone
    screenshot = capture_screen_region(LVL_REGION)
    if screenshot is None:
        print("❌ Impossible de capturer la zone de test")
        return False

    # Test avec différents seuils
    thresholds = [0.3, 0.4, 0.5, 0.6, 0.7]
    for threshold in thresholds:
        detected = detect_image_in_region(level_template, LVL_REGION, f"Test (seuil {threshold})", threshold)
        if detected:
            print(f"✅ Détection réussie avec seuil {threshold}")
            return True
        else:
            print(f"❌ Pas de détection avec seuil {threshold}")
    
    return False

def main():
    """Fonction principale du script"""
    global is_running, last_check_time, start_time
    
    print("🚀 Démarrage du script de surveillance d'écran avec logique prioritaire...")
    print(f"Image level up: {IMAGE_LEVEL}")
    print(f"Image hors combat: {IMAGE_OUT_FIGHT}")
    print(f"Image de combat: {IMAGE_IN_FIGHT}")
    print(f"Zone principale surveillée: {OUT_FIGHT_REGION}")
    print(f"Zone combat: {IN_FIGHT_REGION}")
    print(f"Vérification toutes les {CHECK_INTERVAL} secondes")
    print(f"Délai entre clics: {CLICK_DELAY} secondes")
    print(f"Mode debug: {'Activé' if DEBUG_MODE else 'Désactivé'}")
    print("Appuyez sur Ctrl+C pour arrêter\n")
    
    # Configuration de pyautogui
    pyautogui.FAILSAFE = True
    pyautogui.PAUSE = 0.05
    
    # Chargement des images templates
    print("📷 Chargement des images templates...")
    level_template = load_template_image(IMAGE_LEVEL)
    template = load_template_image(IMAGE_OUT_FIGHT)
    second_template = load_template_image(IMAGE_IN_FIGHT)
    join_template = load_template_image(IMAGE_JOIN)

    if level_template is None:
        print(f"❌ Impossible de charger l'image level up: {IMAGE_LEVEL}")
        return
    
    if template is None:
        print(f"❌ Impossible de charger l'image hors combat: {IMAGE_OUT_FIGHT}")
        return
    
    if second_template is None:
        print(f"❌ Impossible de charger l'image de combat: {IMAGE_IN_FIGHT}")
        return
    
    if join_template is None:
        print(f"❌ Impossible de charger l'image join: {IMAGE_JOIN}")
        return

    print("✅ Images templates chargées avec succès")
    
    # Optionnelle : sans elle, le secours par la fenêtre Spectateur est désactivé
    group_icon_template = load_template_image(IMAGE_GROUP_ICON)
    if group_icon_template is None:
        print("⚠️ Secours Spectateur désactivé (icône de groupe à capturer avec setup_gui.py)")
    else:
        print(f"✅ Secours Spectateur activé{' (DRY RUN : observation seulement)' if SPECTATOR_DRY_RUN else ''}")
    
    # Créer l'overlay du compteur
    print("🖥️ Création de l'overlay du compteur...")
    create_counter_overlay()
    print("✅ Overlay du compteur créé")
    
    # Test de détection de l'image level up
    print("\n🧪 Exécution du test de détection level up...")
    test_level_up_detection(level_template)
    print("✅ Test terminé, début de la surveillance...\n")
    
    # Démarrage du thread de surveillance
    global watchdog_thread_ref
    watchdog_thread_ref = threading.Thread(target=watchdog_thread, daemon=True)
    watchdog_thread_ref.start()
    debug_print("Thread de surveillance démarré")
    
    # Reset du timer initial
    last_check_time = time.time()
    start_time = time.time()  # Initialiser le compteur de temps pour le restart
    
    try:
        while is_running:
            current_time = time.strftime('%H:%M:%S')
            print(f"\n🔍 Vérification à {current_time}...")
            
            # Mettre à jour l'interface du compteur
            if counter_window:
                counter_window.update()
            
            # Forcer un flush de stdout
            sys.stdout.flush()

            # 1. Vérifier d'abord si level up est présent
            level_detected = detect_image_in_region(level_template, LVL_REGION, "Level up", LVL_CONFIDENCE)
            if level_detected:
                perform_level_up_click()
                time.sleep(CHECK_INTERVAL)
                continue
            else:
                level_detected_fallback = detect_image_in_region(level_template, LVL_REGION_FALLBACK, "Level up fallback", LVL_CONFIDENCE)
                if level_detected_fallback:
                    perform_level_up_click()
                    time.sleep(CHECK_INTERVAL)
                    continue
            
            # 2. Sinon, vérifier si l'image hors combat est présente
            image_detected = detect_image_in_region(template, OUT_FIGHT_REGION, "Image hors combat")
            if image_detected:
                time_since_fight = time.time() - fight_start_time if fight_start_time > 0 else float('inf')
                
                short_fight = time_since_fight < FIGHT_MIN_DURATION
                next_spectator_try = time.time() + SPECTATOR_FIRST_DELAY
                spectator_opened = False
                if short_fight:
                    print(f"⏱️ Combat terminé en {time_since_fight:.0f}s (< {FIGHT_MIN_DURATION}s), skip restart dungeon → détection join directe")
                else:
                    # Petit délai pour laisser le temps à l'interface de réagir
                    time.sleep(2)

                    # Exécuter les clics normaux
                    perform_restart_dungeon()
                    
                    # Petit délai pour laisser le temps à l'interface de réagir
                    time.sleep(1)
                
                # Vérifier si l'image join est présente après les clics
                join_detected = detect_image_in_region(join_template, JOIN_REGION, "Image join", verbose=True)
                
                if join_detected:
                    # L'image join est présente, effectuer le clic join
                    perform_join_click()
                    # Attendre le vrai début du combat avant de chercher à nouveau l'image hors combat
                    perform_fight_sequence(second_template, template)
                else:
                    # Image join non détectée, continuer de surveiller jusqu'à ce qu'elle se présente
                    print("🔍 Image join non détectée, début de la surveillance continue...")

                    while is_running:
                        # Vérifier si l'image join est présente
                        join_detected = detect_image_in_region(join_template, JOIN_REGION, "Image join (surveillance)", verbose=True)
                        
                        if join_detected:
                            print("🎉 Image join détectée pendant la surveillance !")
                            perform_join_click()
                            perform_fight_sequence(second_template, template)
                            break
                        
                        # Après un retry, le jeu n'affiche parfois pas la popup join : passer par la fenêtre Spectateur
                        if short_fight and group_icon_template is not None and time.time() >= next_spectator_try:
                            joined = join_via_spectator(group_icon_template, open_window=not spectator_opened)
                            spectator_opened = True
                            if joined:
                                perform_fight_sequence(second_template, template)
                                break
                            next_spectator_try = time.time() + SPECTATOR_CHECK_INTERVAL
                        
                        # Attendre un peu avant de revérifier
                        time.sleep(2)
                
                # Pause courte avant de reprendre la surveillance principale
                time.sleep(1)
                continue
            
            # Aucune image détectée, attendre avant la prochaine vérification
            debug_print(f"Attente de {CHECK_INTERVAL}s...")
            for i in range(CHECK_INTERVAL):
                time.sleep(1)
                if DEBUG_MODE and i % 2 == 0:
                    print(".", end="", flush=True)
            
            if DEBUG_MODE:
                print()  # Nouvelle ligne après les points
            
    except KeyboardInterrupt:
        print("\n🛑 Script arrêté par l'utilisateur.")
        is_running = False
    except Exception as e:
        print(f"\n💥 Erreur inattendue: {e}")
        debug_print(f"Stack trace complet: {sys.exc_info()}")
        is_running = False
    finally:
        # Fermer l'overlay du compteur
        if counter_window:
            try:
                counter_window.window.destroy()
                counter_window.root.destroy()
                print("✅ Overlay du compteur fermé")
            except:
                pass

if __name__ == "__main__":
    main()