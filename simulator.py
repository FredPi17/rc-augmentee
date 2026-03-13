"""
Simulateur Pi Zero W — Voiture RC
Remplace le Pi pour tester le controller.py sans matériel.
- Écoute les commandes UDP (comme le vrai serveur)
- Génère une vue 3D simplifiée (sol quadrillé + horizon)
- Stream en H264 via ffmpeg sur TCP (même protocole que libcamera-vid)
"""

import socket
import json
import threading
import time
import math
import sys
import numpy as np
import cv2

# ─── Configuration ──────────────────────────────────────────────────────────────

UDP_PORT = 5000
VIDEO_PORT = 5001
VIDEO_WIDTH = 640
VIDEO_HEIGHT = 480
VIDEO_FPS = 30

# ─── État simulé ────────────────────────────────────────────────────────────────

state = {
    'direction': 0.0,
    'vitesse': 0.0,
    'camera': 0.0,
}
lock = threading.Lock()
running = True

# Position simulée de la voiture dans le monde
car_x = 0.0
car_z = 0.0
car_angle = 0.0  # radians, 0 = vers le haut (Z+)


# ─── Réception UDP ──────────────────────────────────────────────────────────────

def udp_listener():
    """Écoute les commandes UDP du controller."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(('0.0.0.0', UDP_PORT))
    sock.settimeout(0.1)

    print(f"[UDP] Écoute sur port {UDP_PORT}")

    while running:
        try:
            data, addr = sock.recvfrom(1024)
        except socket.timeout:
            continue
        except OSError:
            break

        try:
            cmd = json.loads(data.decode('utf-8'))
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue

        cmd_type = cmd.get('type')
        value = cmd.get('value')
        if cmd_type is None or value is None:
            continue

        try:
            value = float(value)
        except (TypeError, ValueError):
            continue

        with lock:
            if cmd_type in ('direction', 'vitesse', 'camera'):
                old = state[cmd_type]
                state[cmd_type] = max(-1.0, min(1.0, value))
                if abs(old - value) > 0.01:
                    print(f"[CMD] {cmd_type} = {value:+.2f}")

    sock.close()


# ─── Physique simplifiée ────────────────────────────────────────────────────────

def update_physics(dt):
    """Met à jour la position de la voiture."""
    global car_x, car_z, car_angle

    with lock:
        direction = state['direction']
        vitesse = state['vitesse']

    # Vitesse en unités/seconde
    speed = vitesse * 15.0  # unités monde par seconde
    turn_rate = -direction * 2.5  # négatif : gauche = sens trigo = tourne à gauche à l'écran

    car_angle += turn_rate * dt
    car_x -= math.sin(car_angle) * speed * dt
    car_z -= math.cos(car_angle) * speed * dt


# ─── Rendu 3D simplifié ────────────────────────────────────────────────────────

def project_point(px, pz, cam_x, cam_z, cam_angle, cam_yaw_offset, fov_x, fov_y):
    """
    Projette un point 3D (px, 0, pz) sur l'écran 2D.
    Retourne (screen_x, screen_y) ou None si derrière la caméra.
    """
    # Translation
    dx = px - cam_x
    dz = pz - cam_z

    # Rotation caméra (angle voiture + offset panoramique)
    total_angle = cam_angle + cam_yaw_offset
    cos_a = math.cos(-total_angle)
    sin_a = math.sin(-total_angle)
    rx = dx * cos_a - dz * sin_a
    rz = dx * sin_a + dz * cos_a

    if rz <= 0.1:
        return None

    # Projection perspective
    screen_x = int(VIDEO_WIDTH / 2 + (rx / rz) * fov_x)
    # Hauteur caméra = 0.5 unités
    screen_y = int(VIDEO_HEIGHT / 2 + (0.5 / rz) * fov_y)

    return (screen_x, screen_y)


def render_frame():
    """Génère une frame de la vue caméra."""
    frame = np.zeros((VIDEO_HEIGHT, VIDEO_WIDTH, 3), dtype=np.uint8)

    with lock:
        cam_rotation = state['camera']
        vitesse = state['vitesse']
        direction = state['direction']

    # Caméra suit la voiture
    cam_x = car_x
    cam_z = car_z
    cam_angle = car_angle
    cam_yaw_offset = cam_rotation * math.pi  # panoramique caméra

    fov_x = VIDEO_WIDTH * 0.8
    fov_y = VIDEO_HEIGHT * 0.8

    # ── Ciel dégradé ──
    for y in range(VIDEO_HEIGHT // 2):
        t = y / (VIDEO_HEIGHT // 2)
        r = int(10 + t * 30)
        g = int(10 + t * 50)
        b = int(40 + t * 120)
        frame[y, :] = (r, g, b)

    # ── Sol dégradé ──
    for y in range(VIDEO_HEIGHT // 2, VIDEO_HEIGHT):
        t = (y - VIDEO_HEIGHT // 2) / (VIDEO_HEIGHT // 2)
        g = int(40 + (1 - t) * 30)
        frame[y, :] = (15, g, 10)

    # ── Grille au sol ──
    grid_range = 40
    grid_step = 2

    # Lignes parallèles à X
    for gz in range(-grid_range, grid_range + 1, grid_step):
        prev_pt = None
        for gx_i in range(-grid_range, grid_range + 1):
            gx = gx_i * grid_step
            world_x = gx
            world_z = gz
            pt = project_point(world_x, world_z, cam_x, cam_z,
                               cam_angle, cam_yaw_offset, fov_x, fov_y)
            if pt and prev_pt:
                if (0 <= pt[0] < VIDEO_WIDTH or 0 <= prev_pt[0] < VIDEO_WIDTH):
                    # Couleur par distance
                    dist = math.sqrt((world_x - cam_x) ** 2 + (world_z - cam_z) ** 2)
                    intensity = max(30, min(100, int(120 - dist * 2)))
                    color = (intensity // 2, intensity, intensity // 2)
                    cv2.line(frame, prev_pt, pt, color, 1)
            prev_pt = pt

    # Lignes parallèles à Z
    for gx in range(-grid_range, grid_range + 1, grid_step):
        prev_pt = None
        for gz_i in range(-grid_range, grid_range + 1):
            gz = gz_i * grid_step
            pt = project_point(gx, gz, cam_x, cam_z,
                               cam_angle, cam_yaw_offset, fov_x, fov_y)
            if pt and prev_pt:
                if (0 <= pt[0] < VIDEO_WIDTH or 0 <= prev_pt[0] < VIDEO_WIDTH):
                    dist = math.sqrt((gx - cam_x) ** 2 + (gz - cam_z) ** 2)
                    intensity = max(30, min(100, int(120 - dist * 2)))
                    color = (intensity // 2, intensity, intensity // 2)
                    cv2.line(frame, prev_pt, pt, color, 1)
            prev_pt = pt

    # ── Quelques "pylônes" pour repères visuels ──
    pylons = [
        (10, 10), (-10, 10), (10, -10), (-10, -10),
        (0, 20), (0, -20), (20, 0), (-20, 0),
        (15, 15), (-15, -15), (15, -15), (-15, 15),
        (5, 30), (-5, 30), (5, -30), (-5, -30),
    ]
    for px, pz in pylons:
        base = project_point(px, pz, cam_x, cam_z,
                             cam_angle, cam_yaw_offset, fov_x, fov_y)
        if base:
            # Sommet du pylône (hauteur 1.5 unités)
            dx = px - cam_x
            dz = pz - cam_z
            total_angle = cam_angle + cam_yaw_offset
            cos_a = math.cos(-total_angle)
            sin_a = math.sin(-total_angle)
            rz = dx * sin_a + dz * cos_a
            if rz > 0.1:
                top_y = int(VIDEO_HEIGHT / 2 + ((0.5 - 1.5) / rz) * fov_y)
                dist = math.sqrt(dx ** 2 + dz ** 2)
                if dist < 50:
                    intensity = max(80, min(255, int(300 - dist * 5)))
                    cv2.line(frame, base, (base[0], top_y),
                             (0, 0, intensity), max(1, int(4 - dist / 15)))
                    cv2.circle(frame, (base[0], top_y), max(2, int(5 - dist / 12)),
                               (intensity, intensity // 2, 0), -1)

    # ── Ligne d'horizon ──
    cv2.line(frame, (0, VIDEO_HEIGHT // 2), (VIDEO_WIDTH, VIDEO_HEIGHT // 2),
             (40, 60, 40), 1)

    # ── HUD overlay ──
    overlay = frame.copy()
    cv2.rectangle(overlay, (0, 0), (VIDEO_WIDTH, 35), (0, 0, 0), -1)
    frame = cv2.addWeighted(overlay, 0.6, frame, 0.4, 0)

    font = cv2.FONT_HERSHEY_SIMPLEX
    cv2.putText(frame, "SIMULATEUR", (10, 22), font, 0.6, (0, 200, 255), 1)
    cv2.putText(frame, f"VIT:{vitesse:+.2f}", (180, 22), font, 0.5, (0, 255, 0), 1)
    cv2.putText(frame, f"DIR:{direction:+.2f}", (320, 22), font, 0.5, (0, 180, 255), 1)
    cv2.putText(frame, f"CAM:{cam_rotation:+.2f}", (460, 22),
                font, 0.5, (255, 200, 0), 1)

    # Position dans le monde
    cv2.putText(frame, f"X:{car_x:.0f} Z:{car_z:.0f}", (10, VIDEO_HEIGHT - 10),
                font, 0.4, (100, 100, 100), 1)

    return frame


# ─── Stream vidéo raw RGB via TCP ────────────────────────────────────────────────

def video_streamer():
    """
    Génère les frames en RGB raw et les envoie directement via TCP.
    Pas besoin de ffmpeg côté simulateur — simple et fiable.
    Le controller utilise VideoReceiverRaw pour lire ce flux.
    """

    server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_sock.bind(('0.0.0.0', VIDEO_PORT))
    server_sock.listen(1)
    server_sock.settimeout(1.0)

    print(f"[VIDEO] Serveur TCP raw RGB sur port {VIDEO_PORT}")

    while running:
        print("[VIDEO] En attente de connexion client...")
        client_sock = None
        while running and client_sock is None:
            try:
                client_sock, addr = server_sock.accept()
                client_sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                # Buffer d'envoi plus gros pour éviter les petits paquets
                client_sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 1024 * 1024)
                print(f"[VIDEO] Client connecté depuis {addr}")
            except socket.timeout:
                continue

        if not running:
            break

        interval = 1.0 / VIDEO_FPS
        last_physics = time.monotonic()
        frame_count = 0

        print("[VIDEO] Streaming raw RGB en cours...")

        while running:
            now = time.monotonic()
            dt = now - last_physics
            last_physics = now

            update_physics(dt)
            frame = render_frame()

            # Convertir BGR (OpenCV) → RGB (controller attend du RGB)
            frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

            try:
                client_sock.sendall(frame_rgb.tobytes())
            except (BrokenPipeError, ConnectionResetError, OSError):
                break

            frame_count += 1
            elapsed = time.monotonic() - now
            sleep_time = interval - elapsed
            if sleep_time > 0:
                time.sleep(sleep_time)

        print(f"[VIDEO] Client déconnecté ({frame_count} frames envoyées)")
        try:
            client_sock.close()
        except Exception:
            pass

        if running:
            time.sleep(0.5)

    server_sock.close()
    print("[VIDEO] Stream arrêté")


# ─── Main ───────────────────────────────────────────────────────────────────────

def main():
    global running

    print("=" * 50)
    print("  Simulateur voiture RC")
    print("=" * 50)
    print()
    print(f"  UDP commandes : port {UDP_PORT}")
    print(f"  Vidéo stream  : tcp://0.0.0.0:{VIDEO_PORT}")
    print()
    print("  Dans le controller, connectez-vous à :")
    print(f"    Adresse : localhost (ou 127.0.0.1)")
    print(f"    Port UDP : {UDP_PORT}")
    print(f"    Port vidéo : {VIDEO_PORT}")
    print()
    print("  Ctrl+C pour quitter")
    print()

    # Lancer le listener UDP
    thread_udp = threading.Thread(target=udp_listener, daemon=True)
    thread_udp.start()

    # Le stream vidéo tourne sur le thread principal
    try:
        video_streamer()
    except KeyboardInterrupt:
        pass
    finally:
        running = False
        print("\n[SHUTDOWN] Simulateur arrêté")


if __name__ == '__main__':
    main()
