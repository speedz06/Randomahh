import json
import math
import os
import random
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Set, Tuple

import pygame

try:
    import numpy as np
except Exception:  # numpy ist optional
    np = None

try:
    import tkinter as tk
    from tkinter import filedialog
except Exception:
    tk = None
    filedialog = None


Vec2 = pygame.math.Vector2


@dataclass(frozen=True)
class PieceKey:
    row: int
    col: int


class PuzzlePiece:
    def __init__(
        self,
        pid: int,
        key: PieceKey,
        target_top_left: Tuple[int, int],
        image: pygame.Surface,
        mask_surface: pygame.Surface,
        local_offset: Tuple[int, int],
        edge_tabs: Dict[str, int],
    ):
        self.id = pid
        self.key = key
        self.target_top_left = Vec2(target_top_left)
        self.base_image = image.convert_alpha()
        self.base_mask = mask_surface.convert_alpha()
        self.image = self.base_image
        self.mask_surface = self.base_mask
        self.local_offset = Vec2(local_offset)
        self.edge_tabs = edge_tabs

        self.rotation = 0
        self.cluster_id = pid
        self.pos = Vec2(target_top_left) + Vec2(
            random.randint(-220, 220), random.randint(-180, 180)
        )

        self.cached_shadow = self._build_shadow()

    def _build_shadow(self) -> pygame.Surface:
        shadow = self.mask_surface.copy()
        shadow.fill((0, 0, 0, 90), special_flags=pygame.BLEND_RGBA_MULT)
        return shadow

    def apply_rotation(self, angle: int):
        old_center = self.rect.center
        self.rotation = angle % 360
        self.image = pygame.transform.rotate(self.base_image, self.rotation)
        self.mask_surface = pygame.transform.rotate(self.base_mask, self.rotation)
        self.cached_shadow = self._build_shadow()
        self.pos = Vec2(self.image.get_rect(center=old_center).topleft)

    @property
    def rect(self) -> pygame.Rect:
        return self.mask_surface.get_rect(topleft=(int(self.pos.x), int(self.pos.y)))

    def draw(self, target: pygame.Surface, with_shadow: bool = False) -> pygame.Rect:
        rect = self.rect
        if with_shadow:
            target.blit(self.cached_shadow, (rect.x + 7, rect.y + 7))
        target.blit(self.image, rect)
        return rect


class PieceCluster:
    def __init__(self, cid: int):
        self.id = cid
        self.members: Set[int] = {cid}
        self.z_index = cid


class PuzzleManager:
    EDGE_TOP = "top"
    EDGE_RIGHT = "right"
    EDGE_BOTTOM = "bottom"
    EDGE_LEFT = "left"

    def __init__(
        self,
        board_size=(5, 4),
        piece_size=(110, 110),
        screen_size=(1400, 900),
        theme: str = "aurora",
        mode_name: str = "Classic",
        mode_cfg: Optional[Dict[str, float]] = None,
        image_path: Optional[str] = None,
    ):
        self.cols, self.rows = board_size
        self.pw, self.ph = piece_size
        self.screen_w, self.screen_h = screen_size
        self.theme = theme
        self.image_path = image_path
        self.mode_name = mode_name
        self.mode_cfg = mode_cfg or {
            "snap_threshold": 22,
            "rotation_enabled": 1,
            "rotation_step": 90,
            "randomize_rotation": 0,
        }
        self.tab_radius = int(min(self.pw, self.ph) * 0.18)
        self.tab_depth = int(min(self.pw, self.ph) * 0.32)
        self.edge_samples = 16

        self.ghost_enabled = False
        self.snap_threshold = int(self.mode_cfg["snap_threshold"])

        self.pieces: Dict[int, PuzzlePiece] = {}
        self.parent: Dict[int, int] = {}
        self.clusters: Dict[int, PieceCluster] = {}
        self.cluster_z_order: List[int] = []

        self.drag_cluster_id: Optional[int] = None
        self.drag_anchor_mouse = Vec2()
        self.drag_cluster_origins: Dict[int, Vec2] = {}

        self.board_origin = Vec2(
            (self.screen_w - self.cols * self.pw) // 2,
            (self.screen_h - self.rows * self.ph) // 2,
        )

        self.background = self._create_background_texture(
            self.cols * self.pw, self.rows * self.ph, self.theme, self.image_path
        )
        self.ghost_image = self.background.copy()
        self.ghost_image.set_alpha(85)
        self.snap_flash_until = 0
        self.snap_flash_ids: Set[int] = set()

        self._build_pieces()

    # ----------------- Geometrie -----------------
    def _build_tab_layout(self) -> Dict[Tuple[int, int], Dict[str, int]]:
        tabs: Dict[Tuple[int, int], Dict[str, int]] = {}
        for r in range(self.rows):
            for c in range(self.cols):
                tabs[(r, c)] = {
                    self.EDGE_TOP: 0,
                    self.EDGE_RIGHT: 0,
                    self.EDGE_BOTTOM: 0,
                    self.EDGE_LEFT: 0,
                }

        for r in range(self.rows):
            for c in range(self.cols):
                if c < self.cols - 1:
                    right = random.choice([-1, 1])
                    tabs[(r, c)][self.EDGE_RIGHT] = right
                    tabs[(r, c + 1)][self.EDGE_LEFT] = -right
                if r < self.rows - 1:
                    bottom = random.choice([-1, 1])
                    tabs[(r, c)][self.EDGE_BOTTOM] = bottom
                    tabs[(r + 1, c)][self.EDGE_TOP] = -bottom

        for c in range(self.cols):
            tabs[(0, c)][self.EDGE_TOP] = 0
            tabs[(self.rows - 1, c)][self.EDGE_BOTTOM] = 0
        for r in range(self.rows):
            tabs[(r, 0)][self.EDGE_LEFT] = 0
            tabs[(r, self.cols - 1)][self.EDGE_RIGHT] = 0
        return tabs

    def generate_piece_mask(self, width: int, height: int, tabs: Dict[str, int]) -> Tuple[pygame.Surface, Tuple[int, int]]:
        """
        Erzeugt die Puzzle-Kontur als Alpha-Maske.

        Mathematisches Prinzip (wichtig):
        - Jede Kante wird als parametrisierte Kurve von 0..1 aufgebaut.
        - In der Mitte (u≈0.5) wird optional eine halbkreisähnliche Ausbuchtung eingearbeitet.
        - tab=+1 erzeugt eine Nase (nach außen), tab=-1 eine Bucht (nach innen), tab=0 bleibt flach.
        - Die Nachbarteile passen nahtlos, weil sie dieselbe Kurvenform mit invertiertem Vorzeichen nutzen.
        """
        margin = self.tab_depth + self.tab_radius + 2
        surf_w = width + 2 * margin
        surf_h = height + 2 * margin

        def edge_top(tab: int) -> List[Tuple[float, float]]:
            pts = []
            for i in range(self.edge_samples + 1):
                u = i / self.edge_samples
                x = margin + u * width
                y = margin
                if tab != 0:
                    # Wir formen eine glatte "Nase/Bucht" über einen Halbkreis:
                    # - Der Winkel läuft von pi nach 0, sodass sin() den vertikalen Hub liefert.
                    # - tab=+1 zieht nach außen (nach oben), tab=-1 nach innen.
                    theta = math.pi * abs(u - 0.5) / 0.5
                    if 0.28 <= u <= 0.72:
                        k = math.sin(theta)
                        y -= tab * k * self.tab_depth
                pts.append((x, y))
            return pts

        def edge_right(tab: int) -> List[Tuple[float, float]]:
            pts = []
            for i in range(self.edge_samples + 1):
                u = i / self.edge_samples
                x = margin + width
                y = margin + u * height
                if tab != 0:
                    theta = math.pi * abs(u - 0.5) / 0.5
                    if 0.28 <= u <= 0.72:
                        k = math.sin(theta)
                        x += tab * k * self.tab_depth
                pts.append((x, y))
            return pts

        def edge_bottom(tab: int) -> List[Tuple[float, float]]:
            pts = []
            for i in range(self.edge_samples + 1):
                u = i / self.edge_samples
                x = margin + width - u * width
                y = margin + height
                if tab != 0:
                    theta = math.pi * abs(u - 0.5) / 0.5
                    if 0.28 <= u <= 0.72:
                        k = math.sin(theta)
                        y += tab * k * self.tab_depth
                pts.append((x, y))
            return pts

        def edge_left(tab: int) -> List[Tuple[float, float]]:
            pts = []
            for i in range(self.edge_samples + 1):
                u = i / self.edge_samples
                x = margin
                y = margin + height - u * height
                if tab != 0:
                    theta = math.pi * abs(u - 0.5) / 0.5
                    if 0.28 <= u <= 0.72:
                        k = math.sin(theta)
                        x -= tab * k * self.tab_depth
                pts.append((x, y))
            return pts

        polygon = []
        polygon.extend(edge_top(tabs[self.EDGE_TOP]))
        polygon.extend(edge_right(tabs[self.EDGE_RIGHT])[1:])
        polygon.extend(edge_bottom(tabs[self.EDGE_BOTTOM])[1:])
        polygon.extend(edge_left(tabs[self.EDGE_LEFT])[1:])

        mask_surf = pygame.Surface((surf_w, surf_h), pygame.SRCALPHA)
        pygame.draw.polygon(mask_surf, (255, 255, 255, 255), polygon)
        return mask_surf, (margin, margin)

    # ----------------- Aufbau -----------------
    def _theme_palette(self, theme: str):
        palettes = {
            "aurora": ((70, 220), (80, 220), (95, 225)),
            "sunset": ((110, 245), (70, 200), (70, 170)),
            "ocean": ((40, 130), (95, 220), (120, 245)),
            "mono": ((80, 210), (80, 210), (80, 210)),
        }
        return palettes.get(theme, palettes["aurora"])

    def _create_background_texture(self, w: int, h: int, theme: str, image_path: Optional[str]) -> pygame.Surface:
        if image_path and os.path.exists(image_path):
            try:
                img = pygame.image.load(image_path).convert()
                src_w, src_h = img.get_size()
                src_ratio = src_w / max(1, src_h)
                dst_ratio = w / max(1, h)
                if src_ratio > dst_ratio:
                    new_w = int(src_h * dst_ratio)
                    x = (src_w - new_w) // 2
                    img = img.subsurface((x, 0, new_w, src_h))
                else:
                    new_h = int(src_w / dst_ratio)
                    y = (src_h - new_h) // 2
                    img = img.subsurface((0, y, src_w, new_h))
                return pygame.transform.smoothscale(img, (w, h))
            except Exception:
                pass
        # Keine per-pixel Transparenz im Quellbild: alle Puzzelteile sollen voll deckend sein.
        surf = pygame.Surface((w, h))
        (r_min, r_max), (g_min, g_max), (b_min, b_max) = self._theme_palette(theme)
        if np is not None:
            arr = np.zeros((h, w, 3), dtype=np.uint8)
            x = np.linspace(0, 1, w)
            y = np.linspace(0, 1, h)
            xv, yv = np.meshgrid(x, y)
            base = (np.sin(xv * 12.0) + np.cos(yv * 10.0) + np.sin((xv + yv) * 16.0))
            noise = np.random.normal(0.0, 0.35, (h, w))
            t = np.clip((base + noise + 3.0) / 6.0, 0, 1)
            # Gut sichtbare Palette mit mehr Helligkeit + weichem Kontrast.
            arr[..., 0] = (r_min + (r_max - r_min) * t).astype(np.uint8)
            arr[..., 1] = (g_min + (g_max - g_min) * (1 - t)).astype(np.uint8)
            arr[..., 2] = (b_min + (b_max - b_min) * np.sin(t * math.pi)).astype(np.uint8)
            pygame.surfarray.blit_array(surf, np.transpose(arr, (1, 0, 2)))
        else:
            surf.fill((r_min, g_min, b_min))
            for _ in range(2800):
                cx = random.randint(0, w)
                cy = random.randint(0, h)
                rad = random.randint(4, 28)
                col = (
                    random.randint(r_min, r_max),
                    random.randint(g_min, g_max),
                    random.randint(b_min, b_max),
                )
                pygame.draw.circle(surf, col, (cx, cy), rad)
        return surf

    def _build_pieces(self):
        tabs_map = self._build_tab_layout()
        pid = 0
        margin = self.tab_depth + self.tab_radius + 2

        for r in range(self.rows):
            for c in range(self.cols):
                tabs = tabs_map[(r, c)]
                mask, _ = self.generate_piece_mask(self.pw, self.ph, tabs)

                src_x = c * self.pw - margin
                src_y = r * self.ph - margin
                src_rect = pygame.Rect(src_x, src_y, self.pw + 2 * margin, self.ph + 2 * margin)
                piece_content = pygame.Surface(src_rect.size, pygame.SRCALPHA)
                piece_content.blit(self.background, (0, 0), src_rect)

                piece_img = pygame.Surface(src_rect.size, pygame.SRCALPHA)
                piece_img.blit(piece_content, (0, 0))
                piece_img.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MULT)
                self._enhance_piece_visibility(piece_img, mask)

                piece = PuzzlePiece(
                    pid=pid,
                    key=PieceKey(r, c),
                    target_top_left=(
                        int(self.board_origin.x + c * self.pw - margin),
                        int(self.board_origin.y + r * self.ph - margin),
                    ),
                    image=piece_img,
                    mask_surface=mask,
                    local_offset=(margin, margin),
                    edge_tabs=tabs,
                )

                self.pieces[pid] = piece
                if self.mode_cfg.get("randomize_rotation", 0):
                    step = int(self.mode_cfg.get("rotation_step", 90))
                    piece.apply_rotation(random.choice([0, step, (2 * step) % 360, (3 * step) % 360]))
                self._clamp_piece_on_screen(piece)
                self.parent[pid] = pid
                self.clusters[pid] = PieceCluster(pid)
                self.cluster_z_order.append(pid)
                pid += 1

    def _enhance_piece_visibility(self, piece_img: pygame.Surface, mask: pygame.Surface):
        # Sehr dezente Kontur, damit das Motiv nicht "comic-artig" überzeichnet wirkt.
        m = pygame.mask.from_surface(mask)
        outline = m.outline()
        if len(outline) < 3:
            return

        pygame.draw.lines(piece_img, (20, 24, 30, 70), True, outline, 1)

    def _clamp_piece_on_screen(self, piece: PuzzlePiece):
        rect = piece.rect
        max_x = self.screen_w - rect.width
        max_y = self.screen_h - rect.height
        piece.pos.x = max(0, min(piece.pos.x, max_x))
        piece.pos.y = max(0, min(piece.pos.y, max_y))

    # ----------------- Union-Find -----------------
    def find(self, pid: int) -> int:
        while self.parent[pid] != pid:
            self.parent[pid] = self.parent[self.parent[pid]]
            pid = self.parent[pid]
        return pid

    def union(self, a: int, b: int):
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return

        ca, cb = self.clusters[ra], self.clusters[rb]
        if len(ca.members) < len(cb.members):
            ra, rb = rb, ra
            ca, cb = cb, ca

        self.parent[rb] = ra
        for pid in cb.members:
            self.pieces[pid].cluster_id = ra
            ca.members.add(pid)
        del self.clusters[rb]

        if rb in self.cluster_z_order:
            self.cluster_z_order.remove(rb)
        if ra in self.cluster_z_order:
            self.cluster_z_order.remove(ra)
        self.cluster_z_order.append(ra)

    # ----------------- Interaktion -----------------
    def _top_piece_at(self, mouse_pos: Tuple[int, int]) -> Optional[int]:
        mx, my = mouse_pos
        for cid in reversed(self.cluster_z_order):
            members = self.clusters[cid].members
            member_list = sorted(members, key=lambda p: self.pieces[p].id, reverse=True)
            for pid in member_list:
                piece = self.pieces[pid]
                rect = piece.rect
                if not rect.collidepoint(mx, my):
                    continue
                local_x, local_y = mx - rect.x, my - rect.y
                if 0 <= local_x < rect.width and 0 <= local_y < rect.height:
                    if piece.mask_surface.get_at((local_x, local_y))[3] > 0:
                        return pid
        return None

    def start_drag(self, mouse_pos: Tuple[int, int]):
        selected = self._top_piece_at(mouse_pos)
        if selected is None:
            return

        cid = self.find(selected)
        self.drag_cluster_id = cid
        self.drag_anchor_mouse = Vec2(mouse_pos)
        self.drag_cluster_origins = {pid: self.pieces[pid].pos.copy() for pid in self.clusters[cid].members}

        if cid in self.cluster_z_order:
            self.cluster_z_order.remove(cid)
        self.cluster_z_order.append(cid)

    def update_drag(self, mouse_pos: Tuple[int, int]) -> List[pygame.Rect]:
        if self.drag_cluster_id is None:
            return []

        cid = self.drag_cluster_id
        delta = Vec2(mouse_pos) - self.drag_anchor_mouse
        dirty: List[pygame.Rect] = []
        for pid in self.clusters[cid].members:
            piece = self.pieces[pid]
            old_rect = piece.rect
            piece.pos = self.drag_cluster_origins[pid] + delta
            dirty.append(old_rect)
            dirty.append(piece.rect)
        return dirty

    def end_drag(self) -> bool:
        if self.drag_cluster_id is None:
            return False
        cid = self.drag_cluster_id
        snapped = self._snap_cluster(cid)
        for pid in self.clusters[cid].members:
            self._clamp_piece_on_screen(self.pieces[pid])
        self.drag_cluster_id = None
        return snapped

    def rotate_piece(self, mouse_pos: Tuple[int, int]):
        if not self.mode_cfg.get("rotation_enabled", 1):
            return
        selected = self._top_piece_at(mouse_pos)
        if selected is None:
            return
        piece = self.pieces[selected]
        step = int(self.mode_cfg.get("rotation_step", 90))
        piece.apply_rotation(piece.rotation + step)

    def _neighbor_id(self, key: PieceKey, dr: int, dc: int) -> Optional[int]:
        nr, nc = key.row + dr, key.col + dc
        if not (0 <= nr < self.rows and 0 <= nc < self.cols):
            return None
        return nr * self.cols + nc

    def _snap_cluster(self, cid: int) -> bool:
        members = list(self.clusters[cid].members)

        for pid in members:
            piece = self.pieces[pid]
            if piece.rotation != 0:
                continue

            neighbors = [
                self._neighbor_id(piece.key, -1, 0),
                self._neighbor_id(piece.key, 1, 0),
                self._neighbor_id(piece.key, 0, -1),
                self._neighbor_id(piece.key, 0, 1),
            ]

            for nid in neighbors:
                if nid is None:
                    continue
                n_piece = self.pieces[nid]
                if n_piece.rotation != 0:
                    continue

                other_cluster = self.find(nid)
                if other_cluster == cid:
                    continue

                expected = piece.target_top_left - n_piece.target_top_left
                actual = piece.pos - n_piece.pos
                if (actual - expected).length() <= self.snap_threshold:
                    shift = (n_piece.pos + expected) - piece.pos
                    for mpid in self.clusters[cid].members:
                        self.pieces[mpid].pos += shift
                    self.union(cid, other_cluster)
                    cid = self.find(cid)
                    self.snap_flash_ids = set(self.clusters[cid].members)
                    self.snap_flash_until = pygame.time.get_ticks() + 220
                    return True
        return False

    # ----------------- Rendering -----------------
    def _draw_background_layer(self, screen: pygame.Surface):
        screen.fill((34, 36, 44))
        board_rect = pygame.Rect(
            int(self.board_origin.x),
            int(self.board_origin.y),
            self.cols * self.pw,
            self.rows * self.ph,
        )
        pygame.draw.rect(screen, (54, 58, 72), board_rect.inflate(12, 12), border_radius=8)
        if self.ghost_enabled:
            screen.blit(self.ghost_image, board_rect.topleft)

    def draw_full(self, screen: pygame.Surface):
        self._draw_background_layer(screen)
        active = self.drag_cluster_id

        for cid in self.cluster_z_order:
            if cid == active:
                continue
            for pid in sorted(self.clusters[cid].members):
                self.pieces[pid].draw(screen)

        if active is not None:
            for pid in sorted(self.clusters[active].members):
                self.pieces[pid].draw(screen, with_shadow=True)

        self._draw_snap_flash(screen)

    def draw_dirty(self, screen: pygame.Surface, dirty_rects: List[pygame.Rect]):
        if not dirty_rects:
            return

        merged = dirty_rects[0].copy()
        for r in dirty_rects[1:]:
            merged.union_ip(r)
        merged.inflate_ip(20, 20)
        merged.clamp_ip(screen.get_rect())

        # Dirty-Rect: nur den betroffenen Bereich des Hintergrunds zurücksetzen.
        region = pygame.Surface((merged.width, merged.height), pygame.SRCALPHA)
        region.fill((34, 36, 44))

        board_rect = pygame.Rect(
            int(self.board_origin.x),
            int(self.board_origin.y),
            self.cols * self.pw,
            self.rows * self.ph,
        )
        if merged.colliderect(board_rect.inflate(12, 12)):
            pygame.draw.rect(
                region,
                (54, 58, 72),
                board_rect.inflate(12, 12).move(-merged.x, -merged.y),
                border_radius=8,
            )

        if self.ghost_enabled:
            inter = merged.clip(board_rect)
            if inter.width > 0 and inter.height > 0:
                region.blit(
                    self.ghost_image,
                    (inter.x - merged.x, inter.y - merged.y),
                    pygame.Rect(inter.x - board_rect.x, inter.y - board_rect.y, inter.w, inter.h),
                )

        active = self.drag_cluster_id
        for cid in self.cluster_z_order:
            for pid in sorted(self.clusters[cid].members):
                piece = self.pieces[pid]
                if piece.rect.colliderect(merged):
                    temp = pygame.Surface((piece.rect.w, piece.rect.h), pygame.SRCALPHA)
                    if cid == active:
                        temp.blit(piece.cached_shadow, (7, 7))
                    temp.blit(piece.image, (0, 0))
                    region.blit(temp, (piece.rect.x - merged.x, piece.rect.y - merged.y))

        screen.blit(region, merged.topleft)
        pygame.display.update([merged])

    def _draw_snap_flash(self, screen: pygame.Surface):
        if pygame.time.get_ticks() > self.snap_flash_until:
            return
        for pid in self.snap_flash_ids:
            if pid not in self.pieces:
                continue
            pygame.draw.rect(screen, (255, 240, 120), self.pieces[pid].rect.inflate(6, 6), width=2, border_radius=6)

    def is_solved(self) -> bool:
        roots = {self.find(pid) for pid in self.pieces.keys()}
        if len(roots) != 1:
            return False
        return all(piece.rotation == 0 for piece in self.pieces.values())

    # ----------------- Save / Load -----------------
    def get_state(self) -> Dict:
        return {
            "rows": self.rows,
            "cols": self.cols,
            "pieces": [
                {
                    "id": p.id,
                    "x": p.pos.x,
                    "y": p.pos.y,
                    "rotation": p.rotation,
                    "cluster": self.find(p.id),
                }
                for p in self.pieces.values()
            ],
        }

    def save_to_file(self, path: str):
        data = self.get_state()
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    def load_state(self, data: Dict):
        for pid in self.pieces:
            self.parent[pid] = pid
            self.clusters[pid] = PieceCluster(pid)
            self.pieces[pid].cluster_id = pid

        cluster_members: Dict[int, List[int]] = {}
        for item in data.get("pieces", []):
            pid = item["id"]
            if pid not in self.pieces:
                continue
            p = self.pieces[pid]
            p.pos = Vec2(item["x"], item["y"])
            p.apply_rotation(int(item.get("rotation", 0)) % 360)
            self._clamp_piece_on_screen(p)
            root = int(item.get("cluster", pid))
            cluster_members.setdefault(root, []).append(pid)

        for _, members in cluster_members.items():
            if not members:
                continue
            leader = members[0]
            for pid in members[1:]:
                self.union(leader, pid)

        unique_roots = []
        seen = set()
        for pid in sorted(self.pieces.keys()):
            root = self.find(pid)
            if root not in seen:
                seen.add(root)
                unique_roots.append(root)
        self.cluster_z_order = unique_roots

    def load_from_file(self, path: str):
        if not os.path.exists(path):
            return
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.load_state(data)


class MainGame:
    STATE_MENU = "menu"
    STATE_PLAY = "play"

    def __init__(self):
        pygame.init()
        pygame.display.set_caption("High-End Jigsaw Puzzle")
        base_resolutions = [(1280, 720), (1366, 768), (1600, 900), (1920, 1080), (1920, 1200)]
        desktop_sizes = pygame.display.get_desktop_sizes()
        if desktop_sizes:
            # Laptop/Desktop-native Auflösung zusätzlich anbieten.
            native = desktop_sizes[0]
            if native not in base_resolutions:
                base_resolutions.append(native)
        self.resolutions = base_resolutions
        self.current_res_idx = self.resolutions.index((1920, 1200)) if (1920, 1200) in self.resolutions else 2
        self.screen = pygame.display.set_mode(self.resolutions[self.current_res_idx])
        self.clock = pygame.time.Clock()
        self.font = pygame.font.SysFont("segoeui", 22)
        self.font_small = pygame.font.SysFont("segoeui", 18)
        self.font_title = pygame.font.SysFont("segoeui", 42, bold=True)
        self.piece_count_options = [4, 5, 6, 8, 10]
        self.themes = ["aurora", "sunset", "ocean", "mono"]
        self.modes = [
            ("Casual", {"snap_threshold": 34, "rotation_enabled": 0, "rotation_step": 90, "randomize_rotation": 0}),
            ("Classic", {"snap_threshold": 24, "rotation_enabled": 1, "rotation_step": 90, "randomize_rotation": 0}),
            ("Expert", {"snap_threshold": 14, "rotation_enabled": 1, "rotation_step": 90, "randomize_rotation": 1}),
            ("Hardcore", {"snap_threshold": 10, "rotation_enabled": 1, "rotation_step": 15, "randomize_rotation": 1}),
        ]
        self.current_piece_idx = 2  # default 6x6
        self.current_theme_idx = 0
        self.current_mode_idx = 1
        self.menu_cursor = 0
        self.menu_click_targets: Dict[str, pygame.Rect] = {}
        self.manager: Optional[PuzzleManager] = None
        self.current_slot: Optional[int] = None
        self.solved_cleanup_done = False
        self.current_image_path: Optional[str] = None
        self.last_autosave_ts = time.time()
        self.autosave_interval_sec = 15.0
        self.snap_sound = self._build_snap_sound()
        self.show_overlay = False
        self.running = True
        self.state = self.STATE_MENU

    def _build_manager(self) -> PuzzleManager:
        count = self.piece_count_options[self.current_piece_idx]
        mode_name, mode_cfg = self.modes[self.current_mode_idx]
        theme = self.themes[self.current_theme_idx]
        screen_w, screen_h = self.resolutions[self.current_res_idx]

        board_pixels = min(820, int(min(screen_w, screen_h) * 0.72))
        piece_px = max(44, board_pixels // count)
        return PuzzleManager(
            board_size=(count, count),
            piece_size=(piece_px, piece_px),
            screen_size=(screen_w, screen_h),
            theme=theme,
            mode_name=mode_name,
            mode_cfg=mode_cfg,
            image_path=self.current_image_path,
        )

    def _apply_resolution(self):
        self.screen = pygame.display.set_mode(self.resolutions[self.current_res_idx])

    def _build_snap_sound(self):
        if np is None:
            return None
        try:
            if not pygame.mixer.get_init():
                pygame.mixer.init(frequency=22050, size=-16, channels=1)
            freq = 660
            duration = 0.09
            sr = 22050
            t = np.linspace(0, duration, int(sr * duration), endpoint=False)
            wave = (0.35 * np.sin(2 * np.pi * freq * t) * np.exp(-18 * t) * 32767).astype(np.int16)
            return pygame.sndarray.make_sound(wave)
        except Exception:
            return None

    def _slot_path(self, slot: int) -> str:
        return f"puzzle_save_slot_{slot}.json"

    def _slot_exists(self, slot: int) -> bool:
        return os.path.exists(self._slot_path(slot))

    def _save_to_slot(self, slot: int):
        if self.manager is None:
            return
        payload = {
            "piece_idx": self.current_piece_idx,
            "theme_idx": self.current_theme_idx,
            "mode_idx": self.current_mode_idx,
            "res_idx": self.current_res_idx,
            "image_path": self.current_image_path,
            "puzzle": self.manager.get_state(),
        }
        with open(self._slot_path(slot), "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        self.current_slot = slot

    def _load_slot(self, slot: int) -> bool:
        path = self._slot_path(slot)
        if not os.path.exists(path):
            return False
        with open(path, "r", encoding="utf-8") as f:
            payload = json.load(f)

        self.current_piece_idx = int(payload.get("piece_idx", self.current_piece_idx)) % len(self.piece_count_options)
        self.current_theme_idx = int(payload.get("theme_idx", self.current_theme_idx)) % len(self.themes)
        self.current_mode_idx = int(payload.get("mode_idx", self.current_mode_idx)) % len(self.modes)
        self.current_res_idx = int(payload.get("res_idx", self.current_res_idx)) % len(self.resolutions)
        self.current_image_path = payload.get("image_path")
        self._apply_resolution()
        self.manager = self._build_manager()
        self.manager.load_state(payload.get("puzzle", {}))
        self.state = self.STATE_PLAY
        self.current_slot = slot
        self.solved_cleanup_done = False
        self.last_autosave_ts = time.time()
        return True

    def _delete_slot(self, slot: int):
        path = self._slot_path(slot)
        if os.path.exists(path):
            os.remove(path)
        if self.current_slot == slot:
            self.current_slot = None

    def _start_new_game(self):
        self.manager = self._build_manager()
        self.state = self.STATE_PLAY
        self.current_slot = None
        self.solved_cleanup_done = False
        self.last_autosave_ts = time.time()

    def run(self):
        while self.running:
            dirty: List[pygame.Rect] = []
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    self.running = False
                if self.state == self.STATE_MENU:
                    self._handle_menu_event(event)
                else:
                    self._handle_game_event(event, dirty)

            if self.state == self.STATE_MENU:
                self._draw_menu()
                pygame.display.flip()
            else:
                if self.manager is None:
                    self._start_new_game()
                if dirty:
                    self.manager.draw_dirty(self.screen, dirty)
                if self.show_overlay:
                    self._draw_overlay()
                    pygame.display.update([pygame.Rect(0, 0, 1040, 58)])

                if self.manager.is_solved():
                    if not self.solved_cleanup_done and self.current_slot is not None:
                        self._delete_slot(self.current_slot)
                        self.solved_cleanup_done = True
                    self._draw_win_banner()

                if self.current_slot is not None and (time.time() - self.last_autosave_ts) >= self.autosave_interval_sec:
                    self._save_to_slot(self.current_slot)
                    self.last_autosave_ts = time.time()

            self.clock.tick(60)

        pygame.quit()

    def _draw_overlay(self):
        if self.manager is None:
            return
        count = self.piece_count_options[self.current_piece_idx]
        theme = self.themes[self.current_theme_idx]
        mode_name, _ = self.modes[self.current_mode_idx]
        res_w, res_h = self.resolutions[self.current_res_idx]
        info = "LMB: ziehen | RMB: rotieren | H: Ghost | R: neues Puzzle | SHIFT+1..4 speichern | 1..4 laden | ESC: Menü"
        status = f"{count}x{count} | Theme: {theme} | Mode: {mode_name} | {res_w}x{res_h}"
        if self.current_image_path:
            status += " | Bild: custom"
        if self.current_slot is not None:
            status += f" | Aktiver Slot: {self.current_slot}"
        text = self.font.render(info, True, (240, 240, 240))
        text2 = self.font.render(status, True, (210, 225, 245))
        bg = pygame.Surface((max(text.get_width(), text2.get_width()) + 16, 54), pygame.SRCALPHA)
        bg.fill((0, 0, 0, 130))
        self.screen.blit(bg, (0, 0))
        self.screen.blit(text, (8, 6))
        self.screen.blit(text2, (8, 30))

    def _draw_menu(self):
        self.menu_click_targets.clear()
        self.screen.fill((16, 18, 26))
        sw, sh = self.screen.get_size()
        panel = pygame.Rect(int(sw * 0.08), int(sh * 0.08), int(sw * 0.84), int(sh * 0.82))
        pygame.draw.rect(self.screen, (29, 33, 46), panel, border_radius=24)
        pygame.draw.rect(self.screen, (60, 72, 102), panel, width=2, border_radius=24)

        title = self.font_title.render("Jigsaw Studio", True, (242, 245, 255))
        subtitle = self.font.render("Moderne Startoberfläche mit klickbaren Optionen", True, (172, 184, 215))
        self.screen.blit(title, (panel.x + 50, panel.y + 40))
        self.screen.blit(subtitle, (panel.x + 54, panel.y + 96))

        res_w, res_h = self.resolutions[self.current_res_idx]
        count = self.piece_count_options[self.current_piece_idx]
        theme = self.themes[self.current_theme_idx]
        mode_name, _ = self.modes[self.current_mode_idx]
        base_y = panel.y + 180
        base_x = panel.x + 50
        self._draw_option_row("Größe", f"{count} x {count}", base_y, "size", base_x)
        self._draw_option_row("Theme", theme, base_y + 75, "theme", base_x)
        self._draw_option_row("Modus", mode_name, base_y + 150, "mode", base_x)
        self._draw_option_row("Auflösung", f"{res_w} x {res_h}", base_y + 225, "res", base_x)
        image_rect = pygame.Rect(base_x, base_y + 285, 280, 52)
        image_label = "Bild laden" if not self.current_image_path else "Bild ändern"
        self._draw_button(image_rect, image_label, primary=False)
        self.menu_click_targets["image_pick"] = image_rect
        clear_img_rect = pygame.Rect(base_x + 290, base_y + 285, 120, 52)
        self._draw_button(clear_img_rect, "Reset", enabled=self.current_image_path is not None)
        self.menu_click_targets["image_reset"] = clear_img_rect

        start_rect = pygame.Rect(base_x, base_y + 350, 360, 58)
        self._draw_button(start_rect, "Spiel starten", primary=True)
        self.menu_click_targets["start"] = start_rect

        hint = self.font_small.render(
            "Keyboard: Links/Rechts ändern, Enter starten | Maus: alles klickbar",
            True,
            (160, 178, 214),
        )
        self.screen.blit(hint, (base_x, base_y + 430))

        slot_title = self.font.render("Spielstände", True, (230, 236, 249))
        self.screen.blit(slot_title, (panel.x + panel.w - 420, base_y))
        for s in range(1, 5):
            exists = self._slot_exists(s)
            y = base_y + 50 + (s - 1) * 92
            label = f"Slot {s} • {'Fortsetzen' if exists else 'Leer'}"
            rect = pygame.Rect(panel.x + panel.w - 420, y, 280, 70)
            self._draw_button(rect, label, enabled=exists)
            self.menu_click_targets[f"slot_{s}"] = rect
            del_rect = pygame.Rect(panel.x + panel.w - 130, y, 90, 70)
            self._draw_button(del_rect, "Löschen", enabled=exists)
            self.menu_click_targets[f"delete_{s}"] = del_rect
            small = self.font_small.render(f"Klick zum Laden ({s})", True, (150, 166, 198))
            self.screen.blit(small, (panel.x + panel.w - 400, y + 42))

    def _draw_button(self, rect: pygame.Rect, text: str, primary: bool = False, enabled: bool = True):
        mouse = pygame.mouse.get_pos()
        hovered = rect.collidepoint(mouse)
        if not enabled:
            bg = (58, 62, 77)
            fg = (150, 154, 170)
        elif primary:
            bg = (82, 118, 220) if hovered else (70, 104, 200)
            fg = (248, 250, 255)
        else:
            bg = (71, 82, 112) if hovered else (60, 70, 96)
            fg = (232, 238, 250)
        pygame.draw.rect(self.screen, bg, rect, border_radius=12)
        pygame.draw.rect(self.screen, (98, 112, 150), rect, width=2, border_radius=12)
        txt = self.font.render(text, True, fg)
        self.screen.blit(txt, txt.get_rect(center=rect.center))

    def _draw_option_row(self, name: str, value: str, y: int, key: str, x_base: int):
        label = self.font.render(name, True, (205, 220, 245))
        self.screen.blit(label, (x_base, y + 13))

        left_rect = pygame.Rect(x_base + 160, y, 48, 48)
        right_rect = pygame.Rect(x_base + 520, y, 48, 48)
        value_rect = pygame.Rect(x_base + 220, y, 290, 48)

        self._draw_button(left_rect, "‹")
        self._draw_button(right_rect, "›")
        pygame.draw.rect(self.screen, (50, 58, 82), value_rect, border_radius=10)
        pygame.draw.rect(self.screen, (90, 104, 140), value_rect, width=2, border_radius=10)
        text = self.font.render(value, True, (236, 241, 252))
        self.screen.blit(text, text.get_rect(center=value_rect.center))

        self.menu_click_targets[f"{key}_left"] = left_rect
        self.menu_click_targets[f"{key}_right"] = right_rect

    def _handle_menu_event(self, event: pygame.event.Event):
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            self._handle_menu_click(event.pos)
            return
        if event.type != pygame.KEYDOWN:
            return
        key_to_slot = {pygame.K_1: 1, pygame.K_2: 2, pygame.K_3: 3, pygame.K_4: 4}
        if event.key == pygame.K_UP:
            self.menu_cursor = (self.menu_cursor - 1) % 5
        elif event.key == pygame.K_DOWN:
            self.menu_cursor = (self.menu_cursor + 1) % 5
        elif event.key == pygame.K_LEFT:
            self._menu_adjust(-1)
        elif event.key == pygame.K_RIGHT:
            self._menu_adjust(1)
        elif event.key == pygame.K_RETURN:
            self._start_new_game()
            self.manager.draw_full(self.screen)
            pygame.display.flip()
        elif event.key == pygame.K_i:
            self._pick_image_file()
        elif event.key in key_to_slot:
            slot = key_to_slot[event.key]
            if self._load_slot(slot):
                self.manager.draw_full(self.screen)
                pygame.display.flip()

    def _handle_menu_click(self, pos: Tuple[int, int]):
        for key, rect in self.menu_click_targets.items():
            if not rect.collidepoint(pos):
                continue
            if key == "start":
                self._start_new_game()
                self.manager.draw_full(self.screen)
                pygame.display.flip()
            elif key == "size_left":
                self.current_piece_idx = (self.current_piece_idx - 1) % len(self.piece_count_options)
            elif key == "size_right":
                self.current_piece_idx = (self.current_piece_idx + 1) % len(self.piece_count_options)
            elif key == "theme_left":
                self.current_theme_idx = (self.current_theme_idx - 1) % len(self.themes)
            elif key == "theme_right":
                self.current_theme_idx = (self.current_theme_idx + 1) % len(self.themes)
            elif key == "mode_left":
                self.current_mode_idx = (self.current_mode_idx - 1) % len(self.modes)
            elif key == "mode_right":
                self.current_mode_idx = (self.current_mode_idx + 1) % len(self.modes)
            elif key == "res_left":
                self.current_res_idx = (self.current_res_idx - 1) % len(self.resolutions)
                self._apply_resolution()
            elif key == "res_right":
                self.current_res_idx = (self.current_res_idx + 1) % len(self.resolutions)
                self._apply_resolution()
            elif key == "image_pick":
                self._pick_image_file()
            elif key == "image_reset":
                self.current_image_path = None
            elif key.startswith("slot_"):
                slot = int(key.split("_")[1])
                if self._load_slot(slot):
                    self.manager.draw_full(self.screen)
                    pygame.display.flip()
            elif key.startswith("delete_"):
                slot = int(key.split("_")[1])
                self._delete_slot(slot)
            return

    def _menu_adjust(self, direction: int):
        if self.menu_cursor == 0:
            self.current_piece_idx = (self.current_piece_idx + direction) % len(self.piece_count_options)
        elif self.menu_cursor == 1:
            self.current_theme_idx = (self.current_theme_idx + direction) % len(self.themes)
        elif self.menu_cursor == 2:
            self.current_mode_idx = (self.current_mode_idx + direction) % len(self.modes)
        elif self.menu_cursor == 3:
            self.current_res_idx = (self.current_res_idx + direction) % len(self.resolutions)
            self._apply_resolution()

    def _pick_image_file(self):
        if tk is None or filedialog is None:
            return
        root = tk.Tk()
        root.withdraw()
        path = filedialog.askopenfilename(
            title="Puzzle-Bild wählen",
            filetypes=[("Bilddateien", "*.png;*.jpg;*.jpeg;*.bmp"), ("Alle Dateien", "*.*")],
        )
        root.destroy()
        if path:
            self.current_image_path = path

    def _handle_game_event(self, event: pygame.event.Event, dirty: List[pygame.Rect]):
        if self.manager is None:
            return
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            self.manager.start_drag(event.pos)
        elif event.type == pygame.MOUSEMOTION:
            dirty.extend(self.manager.update_drag(event.pos))
        elif event.type == pygame.MOUSEBUTTONUP and event.button == 1:
            snapped = self.manager.end_drag()
            self.manager.draw_full(self.screen)
            pygame.display.flip()
            if snapped and self.snap_sound is not None:
                try:
                    self.snap_sound.play()
                except Exception:
                    pass
        elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 3:
            self.manager.rotate_piece(event.pos)
            self.manager.draw_full(self.screen)
            pygame.display.flip()
        elif event.type == pygame.KEYDOWN:
            key_to_slot = {pygame.K_1: 1, pygame.K_2: 2, pygame.K_3: 3, pygame.K_4: 4}
            mods = pygame.key.get_mods()
            if event.key == pygame.K_h:
                self.manager.ghost_enabled = not self.manager.ghost_enabled
                self.manager.draw_full(self.screen)
                pygame.display.flip()
            elif event.key == pygame.K_F1:
                self.show_overlay = not self.show_overlay
                self.manager.draw_full(self.screen)
                if self.show_overlay:
                    self._draw_overlay()
                pygame.display.flip()
            elif event.key == pygame.K_r:
                self._start_new_game()
                self.manager.draw_full(self.screen)
                pygame.display.flip()
            elif event.key == pygame.K_ESCAPE:
                self.state = self.STATE_MENU
            elif event.key in key_to_slot:
                slot = key_to_slot[event.key]
                if mods & pygame.KMOD_SHIFT:
                    self._save_to_slot(slot)
                else:
                    if self._load_slot(slot):
                        self.manager.draw_full(self.screen)
                        pygame.display.flip()

    def _draw_win_banner(self):
        text = self.font.render("Puzzle abgeschlossen!", True, (255, 235, 120))
        rect = text.get_rect(center=(700, 40))
        pygame.draw.rect(self.screen, (0, 0, 0), rect.inflate(30, 14), border_radius=8)
        self.screen.blit(text, rect)
        pygame.display.update([rect.inflate(40, 20)])


if __name__ == "__main__":
    MainGame().run()
