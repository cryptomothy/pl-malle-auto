import ctypes
import json
import os
import shutil
import subprocess
import sys
import time
import tkinter as tk
from tkinter import messagebox

# Doit être appelé avant toute création de fenêtre, sinon les coordonnées Tkinter
# et pyautogui divergent quand la mise à l'échelle Windows n'est pas à 100 %.
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

import cv2
import keyboard
import pyautogui
from PIL import Image, ImageTk

import main as bot

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

REGION_LABELS = {
    "OUT_FIGHT_REGION": "Zone hors combat",
    "IN_FIGHT_REGION": "Zone en combat (fin de tour)",
    "LVL_REGION": "Zone level up",
    "LVL_REGION_FALLBACK": "Zone level up (fallback)",
    "JOIN_REGION": "Zone bouton rejoindre",
    "SPECTATOR_LIST_REGION": "Spectateur : zone de la fenêtre",
}

# Régions dont l'image de référence a la taille de la zone (remplaçable à la sélection)
REGION_TEMPLATES = {
    "OUT_FIGHT_REGION": bot.IMAGE_OUT_FIGHT,
    "IN_FIGHT_REGION": bot.IMAGE_IN_FIGHT,
    "LVL_REGION": bot.IMAGE_LEVEL,
    "LVL_REGION_FALLBACK": bot.IMAGE_LEVEL,
    "JOIN_REGION": bot.IMAGE_JOIN,
}

# Zones de recherche plus grandes que leur image : l'image se capture séparément
SEARCH_REGION_TEMPLATES = {
    "SPECTATOR_LIST_REGION": bot.IMAGE_GROUP_ICON,
}

STANDALONE_TEMPLATES = {
    "IMAGE_GROUP_ICON": ("Icône de groupe (Spectateur)", bot.IMAGE_GROUP_ICON),
}

CLICK_LABELS = {
    "RESTART_CLICK_POSITIONS": "Clics restart (30 min)",
    "RESTART_DUNGEON_CLICK_POSITIONS": "Clics restart donjon",
    "JOIN_CLICK_POSITIONS": "Clic rejoindre",
    "SPECTATOR_BUTTON_CLICK": "Clic bouton Spectateur",
}

FIXED_CLICK_COUNTS = {
    "RESTART_CLICK_POSITIONS": 3,
    "SPECTATOR_BUTTON_CLICK": 1,
}


def template_path(region_key):
    relative = REGION_TEMPLATES.get(region_key) or SEARCH_REGION_TEMPLATES.get(region_key)
    return os.path.join(BASE_DIR, relative) if relative else None


def template_size(region_key):
    """Retourne (largeur, hauteur) de l'image template associée à la région, ou None"""
    path = template_path(region_key)
    if path is None or not os.path.exists(path):
        return None
    template = cv2.imread(path, cv2.IMREAD_COLOR)
    if template is None:
        return None
    h, w = template.shape[:2]
    return w, h


class SetupApp:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Configuration des positions et régions")
        self.root.geometry("760x820")

        self.regions = {k: list(getattr(bot, k)) for k in bot.REGION_KEYS}
        self.clicks = {k: [list(p) for p in getattr(bot, k)] for k in bot.CLICK_KEYS}
        self.preview_images = []
        self.update_templates = tk.BooleanVar(value=True)

        self._build_layout()
        self.refresh()

    # ------------------------------------------------------------------ UI

    def _build_layout(self):
        container = tk.Frame(self.root)
        container.pack(fill="both", expand=True)

        self.canvas = tk.Canvas(container, highlightthickness=0)
        scrollbar = tk.Scrollbar(container, orient="vertical", command=self.canvas.yview)
        self.content = tk.Frame(self.canvas)
        self.content.bind(
            "<Configure>",
            lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")),
        )
        self.canvas.create_window((0, 0), window=self.content, anchor="nw")
        self.canvas.configure(yscrollcommand=scrollbar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        self.root.bind_all(
            "<MouseWheel>",
            lambda e: self.canvas.yview_scroll(int(-e.delta / 120), "units"),
        )

        bottom = tk.Frame(self.root, pady=8)
        bottom.pack(fill="x")
        tk.Button(bottom, text="Sauvegarder", width=16, command=self.save).pack(side="left", padx=6)
        tk.Button(bottom, text="Réinitialiser aux défauts", width=22, command=self.reset).pack(side="left", padx=6)
        tk.Button(
            bottom, text="Sauvegarder et lancer le bot", width=26, command=self.save_and_launch
        ).pack(side="right", padx=6)

    def refresh(self):
        for child in self.content.winfo_children():
            child.destroy()

        tk.Label(
            self.content, text="Régions (x, y, largeur, hauteur)", font=("Arial", 13, "bold")
        ).pack(anchor="w", padx=10, pady=(10, 4))
        tk.Checkbutton(
            self.content,
            text="Remplacer l'image de référence par la zone sélectionnée",
            variable=self.update_templates,
        ).pack(anchor="w", padx=10)
        for key in bot.REGION_KEYS:
            self._build_region_row(key)

        tk.Label(
            self.content, text="Images de référence seules", font=("Arial", 13, "bold")
        ).pack(anchor="w", padx=10, pady=(16, 4))
        for key in STANDALONE_TEMPLATES:
            self._build_standalone_template_row(key)

        tk.Label(
            self.content, text="Positions de clic (x, y)", font=("Arial", 13, "bold")
        ).pack(anchor="w", padx=10, pady=(16, 4))
        for key in bot.CLICK_KEYS:
            self._build_click_section(key)

    def _build_region_row(self, key):
        row = tk.Frame(self.content, padx=10, pady=3)
        row.pack(fill="x")
        tk.Label(row, text=REGION_LABELS[key], width=28, anchor="w").pack(side="left")
        tk.Label(row, text=str(tuple(self.regions[key])), width=24, anchor="w", fg="blue").pack(side="left")
        tk.Button(row, text="Sélectionner", command=lambda: self.capture_region(key)).pack(side="left", padx=3)
        tk.Button(row, text="Aperçu", command=lambda: self.preview_region(key)).pack(side="left", padx=3)

    def _build_standalone_template_row(self, key):
        label, relative = STANDALONE_TEMPLATES[key]
        path = os.path.join(BASE_DIR, relative)
        exists = os.path.exists(path)
        row = tk.Frame(self.content, padx=10, pady=3)
        row.pack(fill="x")
        tk.Label(row, text=label, width=28, anchor="w").pack(side="left")
        tk.Label(
            row, text=os.path.basename(relative) if exists else "non capturée",
            width=24, anchor="w", fg="blue" if exists else "red",
        ).pack(side="left")
        tk.Button(row, text="Capturer", command=lambda: self.capture_standalone_template(key)).pack(side="left", padx=3)
        tk.Button(row, text="Aperçu", command=lambda: self.preview_image(label, path)).pack(side="left", padx=3)

    def _build_click_section(self, key):
        section = tk.LabelFrame(self.content, text=CLICK_LABELS[key], padx=8, pady=4)
        section.pack(fill="x", padx=10, pady=4)
        fixed = key in FIXED_CLICK_COUNTS

        for index, (x, y) in enumerate(self.clicks[key]):
            row = tk.Frame(section)
            row.pack(fill="x", pady=1)
            tk.Label(row, text=f"#{index + 1}", width=4, anchor="w").pack(side="left")
            tk.Label(row, text=f"({x}, {y})", width=16, anchor="w", fg="blue").pack(side="left")
            tk.Button(row, text="Capturer", command=lambda i=index: self.capture_click(key, i)).pack(side="left", padx=3)
            tk.Button(row, text="Tester", command=lambda px=x, py=y: pyautogui.moveTo(px, py, duration=0.3)).pack(side="left", padx=3)
            if not fixed:
                tk.Button(row, text="Supprimer", command=lambda i=index: self.delete_click(key, i)).pack(side="left", padx=3)

        if not fixed:
            tk.Button(section, text="+ Ajouter un point", command=lambda: self.capture_click(key, None)).pack(anchor="w", pady=(4, 0))

    # --------------------------------------------------------- point capture

    def capture_click(self, key, index):
        """Capture la position de la souris à l'appui sur F8. index=None ajoute un nouveau point."""
        self.root.withdraw()

        popup = tk.Toplevel(self.root)
        popup.overrideredirect(True)
        popup.attributes("-topmost", True)
        popup.configure(bg="black")
        screen_width = popup.winfo_screenwidth()
        popup.geometry(f"420x70+{(screen_width - 420) // 2}+20")
        tk.Label(
            popup,
            text=f"{CLICK_LABELS[key]}\nPlacez la souris puis appuyez sur F8 (Échap pour annuler)",
            fg="white", bg="black", font=("Arial", 10),
        ).pack()
        position_label = tk.Label(popup, text="", fg="lime", bg="black", font=("Arial", 11, "bold"))
        position_label.pack()

        def finish(position):
            popup.destroy()
            if position is not None:
                if index is None:
                    self.clicks[key].append(list(position))
                else:
                    self.clicks[key][index] = list(position)
            self.root.deiconify()
            self.refresh()

        def poll():
            x, y = pyautogui.position()
            position_label.config(text=f"Position: ({x}, {y})")
            if keyboard.is_pressed("f8"):
                finish((x, y))
                return
            if keyboard.is_pressed("esc"):
                finish(None)
                return
            popup.after(30, poll)

        poll()

    def delete_click(self, key, index):
        if len(self.clicks[key]) <= 1:
            messagebox.showwarning("Impossible", "Il faut garder au moins un point.")
            return
        del self.clicks[key][index]
        self.refresh()

    # -------------------------------------------------------- region capture

    def capture_region(self, key):
        def on_selected(region):
            if self.update_templates.get() and key in REGION_TEMPLATES:
                try:
                    self._save_image(template_path(key), region)
                except Exception as e:
                    messagebox.showerror("Erreur", f"Impossible de sauvegarder l'image de référence :\n{e}")
            else:
                size = template_size(key)
                if size and (region[2] < size[0] or region[3] < size[1]):
                    messagebox.showwarning(
                        "Zone trop petite",
                        f"La zone ({region[2]}x{region[3]}) est plus petite que l'image template "
                        f"({size[0]}x{size[1]}). La détection échouera : agrandissez la zone.",
                    )
            self.regions[key] = region
            self.refresh()

        self._select_area(REGION_LABELS[key], self.regions[key], on_selected)

    def capture_standalone_template(self, key):
        label, relative = STANDALONE_TEMPLATES[key]

        def on_selected(region):
            try:
                self._save_image(os.path.join(BASE_DIR, relative), region)
            except Exception as e:
                messagebox.showerror("Erreur", f"Impossible de sauvegarder l'image :\n{e}")
            self.refresh()

        self._select_area(label, None, on_selected)

    def _select_area(self, title, current_region, on_selected):
        """Overlay plein écran pour tracer une zone. on_selected(region) est appelé une fois
        l'overlay disparu de l'écran, la zone peut donc être capturée telle quelle."""
        self.root.withdraw()
        self.root.after(200, lambda: self._open_area_overlay(title, current_region, on_selected))

    def _open_area_overlay(self, title, current_region, on_selected):
        overlay = tk.Toplevel(self.root)
        overlay.overrideredirect(True)
        overlay.attributes("-topmost", True)
        overlay.attributes("-alpha", 0.3)
        width, height = overlay.winfo_screenwidth(), overlay.winfo_screenheight()
        overlay.geometry(f"{width}x{height}+0+0")

        canvas = tk.Canvas(overlay, bg="black", cursor="crosshair", highlightthickness=0)
        canvas.pack(fill="both", expand=True)
        canvas.create_text(
            width // 2, 40,
            text=f"{title} : cliquez-glissez pour sélectionner la zone (Échap ou clic droit pour annuler)",
            fill="white", font=("Arial", 16, "bold"),
        )
        if current_region:
            cx, cy, cw, ch = current_region
            canvas.create_rectangle(cx, cy, cx + cw, cy + ch, outline="yellow", dash=(4, 4), width=2)

        state = {"start": None, "rect": None, "text": None}

        def on_press(event):
            state["start"] = (event.x_root, event.y_root)
            state["rect"] = canvas.create_rectangle(event.x_root, event.y_root, event.x_root, event.y_root, outline="red", width=2)
            state["text"] = canvas.create_text(event.x_root, event.y_root - 12, text="", fill="white", anchor="sw")

        def on_drag(event):
            if state["start"] is None:
                return
            x0, y0 = state["start"]
            canvas.coords(state["rect"], x0, y0, event.x_root, event.y_root)
            canvas.itemconfig(state["text"], text=f"{abs(event.x_root - x0)} x {abs(event.y_root - y0)}")

        def on_release(event):
            if state["start"] is None:
                return
            x0, y0 = state["start"]
            x1, y1 = event.x_root, event.y_root
            region = [min(x0, x1), min(y0, y1), abs(x1 - x0), abs(y1 - y0)]
            if region[2] < 2 or region[3] < 2:
                close()
                messagebox.showwarning("Zone invalide", "La zone sélectionnée est trop petite.")
                return
            overlay.destroy()
            self.root.after(250, lambda: finish(region))

        def finish(region):
            try:
                on_selected(region)
            finally:
                self.root.deiconify()

        def close(event=None):
            overlay.destroy()
            self.root.deiconify()

        def poll_escape():
            if not overlay.winfo_exists():
                return
            if keyboard.is_pressed("esc"):
                close()
                return
            overlay.after(50, poll_escape)

        canvas.bind("<ButtonPress-1>", on_press)
        canvas.bind("<B1-Motion>", on_drag)
        canvas.bind("<ButtonRelease-1>", on_release)
        canvas.bind("<ButtonPress-3>", close)
        overlay.focus_force()
        poll_escape()

    def _save_image(self, path, region):
        """Capture la région et l'enregistre dans path (l'ancienne image est copiée dans images/backup)"""
        screenshot = pyautogui.screenshot(region=tuple(region))
        if os.path.exists(path):
            backup_dir = os.path.join(os.path.dirname(path), "backup")
            os.makedirs(backup_dir, exist_ok=True)
            name, ext = os.path.splitext(os.path.basename(path))
            timestamp = time.strftime("%Y%m%d_%H%M%S")
            shutil.copy2(path, os.path.join(backup_dir, f"{name}_{timestamp}{ext}"))
        screenshot.save(path, format="PNG")
        print(f"Nouvelle image de référence : {path}")

    def preview_region(self, key):
        region = tuple(self.regions[key])
        self.root.withdraw()
        self.root.update()
        self.root.after(200)
        try:
            screenshot = pyautogui.screenshot(region=region)
        finally:
            self.root.deiconify()

        window = tk.Toplevel(self.root)
        window.title(f"Aperçu - {REGION_LABELS[key]}")
        window.attributes("-topmost", True)

        self.preview_images = []
        tk.Label(window, text="Zone capturée").grid(row=0, column=0, padx=10, pady=(8, 2))
        tk.Label(window, text="Image template").grid(row=0, column=1, padx=10, pady=(8, 2))
        tk.Label(window, image=self._scaled_photo(screenshot)).grid(row=1, column=0, padx=10, pady=8)

        path = template_path(key)
        if path and os.path.exists(path):
            tk.Label(window, image=self._scaled_photo(Image.open(path))).grid(row=1, column=1, padx=10, pady=8)
        else:
            tk.Label(window, text=f"Introuvable :\n{path}", fg="red").grid(row=1, column=1, padx=10, pady=8)

    def preview_image(self, label, path):
        if not os.path.exists(path):
            messagebox.showwarning("Introuvable", f"L'image n'a pas encore été capturée :\n{path}")
            return
        window = tk.Toplevel(self.root)
        window.title(f"Aperçu - {label}")
        window.attributes("-topmost", True)
        self.preview_images = []
        tk.Label(window, image=self._scaled_photo(Image.open(path))).pack(padx=10, pady=10)

    def _scaled_photo(self, image):
        """Agrandit les petites images pour qu'elles soient lisibles dans l'aperçu"""
        scale = max(1, min(6, 200 // max(1, max(image.width, image.height))))
        if scale > 1:
            image = image.resize((image.width * scale, image.height * scale), Image.NEAREST)
        photo = ImageTk.PhotoImage(image)
        self.preview_images.append(photo)
        return photo

    # ---------------------------------------------------------- save / launch

    def save(self, notify=True):
        data = {
            "regions": {k: list(v) for k, v in self.regions.items()},
            "clicks": {k: [list(p) for p in v] for k, v in self.clicks.items()},
        }
        with open(bot.CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        print(f"Configuration sauvegardée dans {bot.CONFIG_FILE}")
        if notify:
            messagebox.showinfo("Sauvegardé", f"Configuration sauvegardée dans\n{bot.CONFIG_FILE}")

    def reset(self):
        if not messagebox.askyesno("Réinitialiser", "Remettre toutes les valeurs par défaut de main.py ?"):
            return
        self.regions = {k: list(v) for k, v in bot.DEFAULT_CONFIG["regions"].items()}
        self.clicks = {k: [list(p) for p in v] for k, v in bot.DEFAULT_CONFIG["clicks"].items()}
        self.refresh()

    def save_and_launch(self):
        self.save(notify=False)
        creationflags = subprocess.CREATE_NEW_CONSOLE if os.name == "nt" else 0
        subprocess.Popen(
            [sys.executable, os.path.join(BASE_DIR, "main.py")],
            cwd=BASE_DIR,
            creationflags=creationflags,
        )
        self.root.destroy()

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    SetupApp().run()
