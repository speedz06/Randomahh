import json
import math
import os
import random
from dataclasses import dataclass
from typing import Dict, List, Optional, Set, Tuple

import pygame

try:
    import numpy as np
except Exception:  # numpy ist optional
    np = None


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

    def __init__(self, board_size=(5, 4), piece_size=(110, 110), screen_size=(1400, 900)):
        self.cols, self.rows = board_size
        self.pw, self.ph = piece_size
        self.screen_w, self.screen_h = screen_size
        self.tab_radius = int(min(self.pw, self.ph) * 0.18)
        self.tab_depth = int(min(self.pw, self.ph) * 0.32)
        self.edge_samples = 16

        self.ghost_enabled = False
        self.snap_threshold = 22

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

        self.background = self._create_background_texture(self.cols * self.pw, self.rows * self.ph)
        self.ghost_image = self.background.copy()
        self.ghost_image.set_alpha(85)

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
    def _create_background_texture(self, w: int, h: int) -> pygame.Surface:
        surf = pygame.Surface((w, h), pygame.SRCALPHA)
        if np is not None:
            arr = np.zeros((h, w, 3), dtype=np.uint8)
            x = np.linspace(0, 1, w)
            y = np.linspace(0, 1, h)
            xv, yv = np.meshgrid(x, y)
            base = (np.sin(xv * 12.0) + np.cos(yv * 10.0) + np.sin((xv + yv) * 16.0))
            noise = np.random.normal(0.0, 0.35, (h, w))
            t = np.clip((base + noise + 3.0) / 6.0, 0, 1)
            # Gut sichtbare Palette mit mehr Helligkeit + weichem Kontrast.
            arr[..., 0] = (70 + 150 * t).astype(np.uint8)
            arr[..., 1] = (80 + 140 * (1 - t)).astype(np.uint8)
            arr[..., 2] = (95 + 130 * np.sin(t * math.pi)).astype(np.uint8)
            pygame.surfarray.blit_array(surf, np.transpose(arr, (1, 0, 2)))
        else:
            surf.fill((64, 70, 90))
            for _ in range(2800):
                cx = random.randint(0, w)
                cy = random.randint(0, h)
                rad = random.randint(4, 28)
                col = (
                    random.randint(80, 210),
                    random.randint(70, 210),
                    random.randint(90, 220),
                    random.randint(22, 62),
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
                self.parent[pid] = pid
                self.clusters[pid] = PieceCluster(pid)
                self.cluster_z_order.append(pid)
                pid += 1

    def _enhance_piece_visibility(self, piece_img: pygame.Surface, mask: pygame.Surface):
        # Dezent hellere Kantenbeleuchtung + dunkler Außenrand,
        # damit die Verzahnungen auch auf ähnlichen Farbbereichen lesbar bleiben.
        m = pygame.mask.from_surface(mask)
        outline = m.outline()
        if len(outline) < 3:
            return

        pygame.draw.lines(piece_img, (15, 18, 24, 210), True, outline, 3)
        pygame.draw.lines(piece_img, (230, 236, 246, 165), True, outline, 1)

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

    def end_drag(self):
        if self.drag_cluster_id is None:
            return
        cid = self.drag_cluster_id
        self._snap_cluster(cid)
        self.drag_cluster_id = None

    def rotate_piece(self, mouse_pos: Tuple[int, int]):
        selected = self._top_piece_at(mouse_pos)
        if selected is None:
            return
        piece = self.pieces[selected]
        piece.apply_rotation(piece.rotation + 90)

    def _neighbor_id(self, key: PieceKey, dr: int, dc: int) -> Optional[int]:
        nr, nc = key.row + dr, key.col + dc
        if not (0 <= nr < self.rows and 0 <= nc < self.cols):
            return None
        return nr * self.cols + nc

    def _snap_cluster(self, cid: int):
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
                    return

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

    def is_solved(self) -> bool:
        roots = {self.find(pid) for pid in self.pieces.keys()}
        if len(roots) != 1:
            return False
        return all(piece.rotation == 0 for piece in self.pieces.values())

    # ----------------- Save / Load -----------------
    def save_to_file(self, path: str):
        data = {
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
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    def load_from_file(self, path: str):
        if not os.path.exists(path):
            return

        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

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


class MainGame:
    def __init__(self):
        pygame.init()
        pygame.display.set_caption("High-End Jigsaw Puzzle")
        self.screen = pygame.display.set_mode((1400, 900))
        self.clock = pygame.time.Clock()
        self.font = pygame.font.SysFont("consolas", 20)
        self.manager = PuzzleManager(board_size=(10, 7), piece_size=(90, 90), screen_size=(1400, 900))
        self.running = True
        self.save_path = "puzzle_save.json"

    def run(self):
        self.manager.draw_full(self.screen)
        pygame.display.flip()

        while self.running:
            dirty: List[pygame.Rect] = []
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    self.running = False

                elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                    self.manager.start_drag(event.pos)

                elif event.type == pygame.MOUSEMOTION:
                    dirty.extend(self.manager.update_drag(event.pos))

                elif event.type == pygame.MOUSEBUTTONUP and event.button == 1:
                    self.manager.end_drag()
                    self.manager.draw_full(self.screen)
                    pygame.display.flip()

                elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 3:
                    self.manager.rotate_piece(event.pos)
                    self.manager.draw_full(self.screen)
                    pygame.display.flip()

                elif event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_h:
                        self.manager.ghost_enabled = not self.manager.ghost_enabled
                        self.manager.draw_full(self.screen)
                        pygame.display.flip()
                    elif event.key == pygame.K_s:
                        self.manager.save_to_file(self.save_path)
                    elif event.key == pygame.K_l:
                        self.manager.load_from_file(self.save_path)
                        self.manager.draw_full(self.screen)
                        pygame.display.flip()
                    elif event.key == pygame.K_r:
                        self.manager = PuzzleManager(board_size=(10, 7), piece_size=(90, 90), screen_size=(1400, 900))
                        self.manager.draw_full(self.screen)
                        pygame.display.flip()

            if dirty:
                self.manager.draw_dirty(self.screen, dirty)

            self._draw_overlay()
            pygame.display.update([pygame.Rect(0, 0, 700, 32)])

            if self.manager.is_solved():
                self._draw_win_banner()

            self.clock.tick(60)

        pygame.quit()

    def _draw_overlay(self):
        info = "LMB: ziehen | RMB: rotieren | H: Ghost | S/L: Save/Load | R: neu"
        text = self.font.render(info, True, (240, 240, 240))
        bg = pygame.Surface((text.get_width() + 16, 30), pygame.SRCALPHA)
        bg.fill((0, 0, 0, 130))
        self.screen.blit(bg, (0, 0))
        self.screen.blit(text, (8, 6))

    def _draw_win_banner(self):
        text = self.font.render("Puzzle abgeschlossen!", True, (255, 235, 120))
        rect = text.get_rect(center=(700, 40))
        pygame.draw.rect(self.screen, (0, 0, 0), rect.inflate(30, 14), border_radius=8)
        self.screen.blit(text, rect)
        pygame.display.update([rect.inflate(40, 20)])


if __name__ == "__main__":
    MainGame().run()
