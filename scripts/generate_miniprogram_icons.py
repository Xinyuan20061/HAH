"""Generate HealthMate's deterministic mini-program icon set.

The source of truth is code so normal/selected states keep identical geometry.
Run with the repository Python environment (Pillow is required).
"""

from pathlib import Path
import sys

from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parents[1] / "miniprogram" / "assets"
SCALE = 4
INK = "#111613"
MUTED = "#8e9389"
LIME = "#c4e267"
GREEN = "#506336"


def pt(value):
    if isinstance(value, (tuple, list)):
        return tuple(int(round(item * SCALE)) for item in value)
    return int(round(value * SCALE))


def canvas(size=96):
    image = Image.new("RGBA", (size * SCALE, size * SCALE), (0, 0, 0, 0))
    return image, ImageDraw.Draw(image)


def line(draw, points, color, width=5, joint="curve"):
    draw.line([pt(item) for item in points], fill=color, width=pt(width), joint=joint)


def rounded(draw, box, radius, color, width=5, fill=None):
    scaled_box = pt(box)
    scaled_radius = pt(radius)
    scaled_width = pt(width)
    if hasattr(draw, "rounded_rectangle"):
        draw.rounded_rectangle(scaled_box, radius=scaled_radius, outline=color, width=scaled_width, fill=fill)
        return

    # Pillow 7.x compatibility for the lightweight visual-audit environment.
    x0, y0, x1, y1 = scaled_box
    r = scaled_radius
    if fill is not None:
        draw.rectangle((x0 + r, y0, x1 - r, y1), fill=fill)
        draw.rectangle((x0, y0 + r, x1, y1 - r), fill=fill)
        draw.ellipse((x0, y0, x0 + 2 * r, y0 + 2 * r), fill=fill)
        draw.ellipse((x1 - 2 * r, y0, x1, y0 + 2 * r), fill=fill)
        draw.ellipse((x0, y1 - 2 * r, x0 + 2 * r, y1), fill=fill)
        draw.ellipse((x1 - 2 * r, y1 - 2 * r, x1, y1), fill=fill)
    if color:
        draw.line((x0 + r, y0, x1 - r, y0), fill=color, width=scaled_width)
        draw.line((x0 + r, y1, x1 - r, y1), fill=color, width=scaled_width)
        draw.line((x0, y0 + r, x0, y1 - r), fill=color, width=scaled_width)
        draw.line((x1, y0 + r, x1, y1 - r), fill=color, width=scaled_width)
        draw.arc((x0, y0, x0 + 2 * r, y0 + 2 * r), 180, 270, fill=color, width=scaled_width)
        draw.arc((x1 - 2 * r, y0, x1, y0 + 2 * r), 270, 360, fill=color, width=scaled_width)
        draw.arc((x0, y1 - 2 * r, x0 + 2 * r, y1), 90, 180, fill=color, width=scaled_width)
        draw.arc((x1 - 2 * r, y1 - 2 * r, x1, y1), 0, 90, fill=color, width=scaled_width)


def ellipse(draw, box, color, width=5, fill=None):
    draw.ellipse(pt(box), outline=color, width=pt(width), fill=fill)


def spark(draw, color, center=(48, 48), radius=23, width=4):
    x, y = center
    points = [
        (x, y - radius),
        (x + radius * 0.22, y - radius * 0.22),
        (x + radius, y),
        (x + radius * 0.22, y + radius * 0.22),
        (x, y + radius),
        (x - radius * 0.22, y + radius * 0.22),
        (x - radius, y),
        (x - radius * 0.22, y - radius * 0.22),
        (x, y - radius),
    ]
    line(draw, points, color, width)


def tab_home(draw, color):
    line(draw, [(19, 45), (48, 21), (77, 45)], color)
    rounded(draw, (26, 40, 70, 76), 7, color)
    line(draw, [(43, 76), (43, 57), (57, 57), (57, 76)], color)


def tab_agent(draw, color):
    rounded(draw, (17, 20, 79, 73), 22, color)
    line(draw, [(34, 72), (27, 82), (49, 73)], color)
    spark(draw, color, radius=16, width=4)


def tab_records(draw, color):
    rounded(draw, (24, 19, 72, 78), 8, color)
    rounded(draw, (36, 14, 60, 28), 5, color, 4, fill=None)
    line(draw, [(34, 43), (40, 49), (50, 37)], color, 4)
    line(draw, [(55, 44), (65, 44)], color, 4)
    line(draw, [(34, 61), (40, 67), (50, 55)], color, 4)
    line(draw, [(55, 62), (65, 62)], color, 4)


def tab_plan(draw, color):
    rounded(draw, (19, 24, 77, 77), 9, color)
    line(draw, [(19, 40), (77, 40)], color, 4)
    line(draw, [(34, 17), (34, 30)], color, 5)
    line(draw, [(62, 17), (62, 30)], color, 5)
    line(draw, [(33, 57), (43, 66), (64, 48)], color, 5)


def tab_profile(draw, color):
    ellipse(draw, (34, 18, 62, 46), color)
    draw.arc(pt((20, 48, 76, 86)), 190, 350, fill=color, width=pt(5))


TAB_DRAWERS = {
    "home": tab_home,
    "agent": tab_agent,
    "records": tab_records,
    "plan": tab_plan,
    "profile": tab_profile,
}


def tab_gym(draw, color):
    line(draw, [(27, 48), (69, 48)], color, 6)
    rounded(draw, (18, 34, 29, 62), 4, color, 5)
    rounded(draw, (67, 34, 78, 62), 4, color, 5)
    line(draw, [(13, 40), (13, 56)], color, 5)
    line(draw, [(83, 40), (83, 56)], color, 5)


def tab_dashboard(draw, color):
    rounded(draw, (19, 19, 43, 43), 7, color, 4)
    rounded(draw, (53, 19, 77, 43), 7, color, 4)
    rounded(draw, (19, 53, 43, 77), 7, color, 4)
    rounded(draw, (53, 53, 77, 77), 7, color, 4, fill=color)


def tab_steward(draw, color):
    rounded(draw, (17, 20, 79, 73), 22, color)
    line(draw, [(34, 72), (27, 82), (49, 73)], color)
    spark(draw, color, radius=16, width=4)


SHELL_TAB_DRAWERS = {
    "gym": tab_gym,
    "dashboard": tab_dashboard,
    "steward": tab_steward,
}


def save(image, path, size):
    path.parent.mkdir(parents=True, exist_ok=True)
    resampling = Image.Resampling.LANCZOS if hasattr(Image, "Resampling") else Image.LANCZOS
    image.resize((size, size), resampling).save(path, optimize=True)


def generate_tabs():
    for name, draw_icon in TAB_DRAWERS.items():
        for active in (False, True):
            image, draw = canvas()
            draw_icon(draw, INK if active else MUTED)
            if active:
                draw.ellipse(pt((72, 14, 84, 26)), fill=LIME)
                draw.ellipse(pt((75, 17, 81, 23)), fill=INK)
            suffix = "-active" if active else ""
            save(image, ROOT / "tabbar" / f"{name}{suffix}.png", 81)


def generate_shell_tabs():
    for name, draw_icon in SHELL_TAB_DRAWERS.items():
        for active in (False, True):
            image, draw = canvas()
            draw_icon(draw, INK if active else MUTED)
            suffix = "-active" if active else ""
            save(image, ROOT / "tabbar" / f"{name}{suffix}.png", 81)


def icon_spark(draw, color):
    draw.ellipse(pt((12, 12, 84, 84)), fill=LIME)
    spark(draw, color, radius=22, width=4)


def icon_settings(draw, color):
    line(draw, [(20, 30), (76, 30)], color, 4)
    line(draw, [(20, 48), (76, 48)], color, 4)
    line(draw, [(20, 66), (76, 66)], color, 4)
    ellipse(draw, (31, 23, 45, 37), color, 4, fill="#ffffff")
    ellipse(draw, (55, 41, 69, 55), color, 4, fill="#ffffff")
    ellipse(draw, (38, 59, 52, 73), color, 4, fill="#ffffff")


def icon_send(draw, color):
    draw.ellipse(pt((10, 10, 86, 86)), fill=LIME)
    line(draw, [(48, 67), (48, 31)], color, 5)
    line(draw, [(33, 45), (48, 30), (63, 45)], color, 5)


def icon_meal(draw, color):
    draw.arc(pt((20, 27, 76, 75)), 0, 180, fill=color, width=pt(5))
    line(draw, [(25, 51), (71, 51)], color, 5)
    line(draw, [(38, 75), (58, 75)], color, 5)
    draw.arc(pt((48, 16, 72, 42)), 180, 300, fill=GREEN, width=pt(4))


def icon_calendar(draw, color):
    rounded(draw, (18, 23, 78, 78), 10, color)
    line(draw, [(18, 40), (78, 40)], color, 4)
    line(draw, [(34, 17), (34, 30)], color, 5)
    line(draw, [(62, 17), (62, 30)], color, 5)
    line(draw, [(32, 58), (42, 67), (64, 49)], color, 5)


def icon_recovery(draw, color):
    draw.arc(pt((18, 16, 76, 78)), 70, 285, fill=color, width=pt(5))
    line(draw, [(61, 22), (67, 22)], GREEN, 4)
    line(draw, [(64, 19), (64, 25)], GREEN, 4)


def icon_copy(draw, color):
    rounded(draw, (29, 20, 73, 64), 8, color, 4)
    rounded(draw, (18, 31, 62, 75), 8, color, 4)


def icon_chevron(draw, color):
    line(draw, [(38, 27), (59, 48), (38, 69)], color, 5)


def icon_shield(draw, color):
    line(draw, [(48, 16), (75, 26), (71, 60), (48, 80), (25, 60), (21, 26), (48, 16)], color, 5)
    line(draw, [(35, 48), (44, 57), (62, 38)], color, 5)


def icon_add(draw, color):
    line(draw, [(48, 25), (48, 71)], color, 5)
    line(draw, [(25, 48), (71, 48)], color, 5)


def icon_close(draw, color):
    line(draw, [(29, 29), (67, 67)], color, 5)
    line(draw, [(67, 29), (29, 67)], color, 5)


def icon_edit(draw, color):
    line(draw, [(25, 69), (30, 51), (59, 22), (74, 37), (45, 66), (25, 69)], color, 5)
    line(draw, [(53, 28), (68, 43)], color, 4)


def icon_check(draw, color):
    line(draw, [(25, 49), (41, 65), (72, 32)], color, 6)


def icon_delete(draw, color):
    rounded(draw, (29, 31, 67, 76), 6, color, 4)
    line(draw, [(24, 25), (72, 25)], color, 5)
    line(draw, [(39, 18), (57, 18)], color, 5)
    line(draw, [(41, 42), (41, 64)], color, 4)
    line(draw, [(55, 42), (55, 64)], color, 4)


def icon_upload(draw, color):
    line(draw, [(48, 63), (48, 25)], color, 5)
    line(draw, [(33, 40), (48, 25), (63, 40)], color, 5)
    line(draw, [(24, 58), (24, 75), (72, 75), (72, 58)], color, 5)


def icon_camera(draw, color):
    rounded(draw, (16, 29, 80, 73), 11, color, 5)
    line(draw, [(29, 29), (36, 21), (60, 21), (67, 29)], color, 5)
    ellipse(draw, (36, 38, 60, 62), color, 5)


def icon_activity(draw, color):
    line(draw, [(14, 51), (28, 51), (37, 31), (49, 69), (59, 42), (67, 51), (82, 51)], color, 5)


def icon_trend(draw, color):
    line(draw, [(20, 69), (37, 51), (50, 59), (74, 31)], color, 5)
    line(draw, [(59, 31), (74, 31), (74, 46)], color, 5)


def icon_target(draw, color):
    ellipse(draw, (18, 18, 78, 78), color, 5)
    ellipse(draw, (32, 32, 64, 64), color, 5)
    ellipse(draw, (43, 43, 53, 53), color, 3, fill=color)


def icon_report(draw, color):
    rounded(draw, (22, 17, 74, 79), 8, color, 5)
    line(draw, [(34, 34), (62, 34)], color, 4)
    line(draw, [(34, 48), (62, 48)], color, 4)
    line(draw, [(34, 62), (54, 62)], color, 4)


def icon_bell(draw, color):
    draw.arc(pt((24, 19, 72, 69)), 180, 360, fill=color, width=pt(5))
    line(draw, [(24, 44), (24, 63), (17, 70), (79, 70), (72, 63), (72, 44)], color, 5)
    draw.arc(pt((39, 65, 57, 82)), 0, 180, fill=color, width=pt(4))


def icon_dumbbell(draw, color):
    line(draw, [(29, 48), (67, 48)], color, 6)
    rounded(draw, (18, 34, 30, 62), 4, color, 5)
    rounded(draw, (66, 34, 78, 62), 4, color, 5)
    line(draw, [(13, 39), (13, 57)], color, 5)
    line(draw, [(83, 39), (83, 57)], color, 5)


def icon_user_card(draw, color):
    rounded(draw, (16, 20, 80, 76), 11, color, 5)
    ellipse(draw, (28, 33, 44, 49), color, 4)
    draw.arc(pt((24, 47, 48, 69)), 190, 350, fill=color, width=pt(4))
    line(draw, [(54, 39), (69, 39)], color, 4)
    line(draw, [(54, 53), (69, 53)], color, 4)


def icon_water(draw, color):
    draw.polygon([pt((48, 14)), pt((72, 50)), pt((72, 61)), pt((65, 74)), pt((48, 80)), pt((31, 74)), pt((24, 61)), pt((24, 50))], outline=color, fill=None)
    line(draw, [(48, 14), (24, 50)], color, 5)
    line(draw, [(48, 14), (72, 50)], color, 5)
    draw.arc(pt((33, 45, 64, 69)), 15, 150, fill=color, width=pt(4))


def icon_sleep(draw, color):
    draw.arc(pt((22, 18, 73, 76)), 70, 290, fill=color, width=pt(5))
    line(draw, [(55, 22), (67, 22), (57, 35), (69, 35)], color, 3)


def icon_steps(draw, color):
    ellipse(draw, (21, 42, 43, 76), color, 4)
    ellipse(draw, (52, 20, 74, 54), color, 4)
    ellipse(draw, (24, 30, 31, 38), color, 3, fill=color)
    ellipse(draw, (65, 58, 72, 66), color, 3, fill=color)


def icon_protein(draw, color):
    ellipse(draw, (21, 29, 75, 70), color, 5)
    draw.arc(pt((29, 34, 67, 63)), 195, 345, fill=color, width=pt(4))
    line(draw, [(48, 28), (48, 69)], color, 3)


def icon_calories(draw, color):
    points = [(50, 14), (67, 37), (61, 36), (73, 55), (70, 70), (58, 80), (38, 80), (24, 68), (23, 52), (34, 33), (39, 50), (50, 38), (50, 14)]
    line(draw, points, color, 5)


def icon_breathe(draw, color):
    draw.arc(pt((16, 31, 58, 61)), 205, 520, fill=color, width=pt(4))
    draw.arc(pt((38, 19, 82, 51)), 205, 520, fill=color, width=pt(4))
    draw.arc(pt((38, 47, 82, 79)), 205, 520, fill=color, width=pt(4))


def icon_warning(draw, color):
    line(draw, [(48, 15), (81, 75), (15, 75), (48, 15)], color, 5)
    line(draw, [(48, 35), (48, 55)], color, 5)
    ellipse(draw, (45, 63, 51, 69), color, 2, fill=color)


def icon_agent_xiaojian(draw, color):
    """Direct, energetic coach: a compact dumbbell on the brand lime."""
    draw.ellipse(pt((8, 8, 88, 88)), fill=LIME)
    line(draw, [(31, 48), (65, 48)], color, 6)
    rounded(draw, (20, 33, 31, 63), 4, color, 5)
    rounded(draw, (65, 33, 76, 63), 4, color, 5)
    line(draw, [(15, 39), (15, 57)], color, 5)
    line(draw, [(81, 39), (81, 57)], color, 5)


def icon_agent_xiaokang(draw, color):
    """Gentle wellbeing companion: a calm two-leaf sprout."""
    draw.ellipse(pt((8, 8, 88, 88)), fill="#e9efd9")
    line(draw, [(48, 72), (48, 45)], GREEN, 4)
    left_leaf = [(47, 57), (35, 38), (19, 34), (23, 50), (36, 60), (47, 61)]
    right_leaf = [(49, 51), (59, 29), (78, 25), (73, 43), (59, 53), (49, 55)]
    draw.polygon([pt(item) for item in left_leaf], fill=LIME)
    draw.polygon([pt(item) for item in right_leaf], fill="#a8c84f")
    line(draw, left_leaf + [left_leaf[0]], GREEN, 3)
    line(draw, right_leaf + [right_leaf[0]], GREEN, 3)
    line(draw, [(48, 55), (34, 45)], GREEN, 3)
    line(draw, [(49, 50), (64, 38)], GREEN, 3)


def icon_agent_steward(draw, color):
    """General health steward: the HealthMate compass spark."""
    draw.ellipse(pt((8, 8, 88, 88)), fill="#ffffff", outline="#e5e8dc", width=pt(3))
    spark(draw, GREEN, radius=24, width=4)
    draw.ellipse(pt((42, 42, 54, 54)), fill=LIME)


def icon_microphone(draw, color):
    """Optically balanced microphone for a small circular touch target."""
    rounded(draw, (35, 17, 61, 57), 13, color, 5)
    draw.arc(pt((25, 34, 71, 72)), 0, 180, fill=color, width=pt(5))
    line(draw, [(48, 71), (48, 80)], color, 5)
    line(draw, [(36, 80), (60, 80)], color, 5)


def icon_speaker(draw, color):
    """Compact playback glyph, distinct from the activity waveform."""
    draw.polygon([pt((23, 41)), pt((35, 41)), pt((51, 28)), pt((51, 68)), pt((35, 55)), pt((23, 55))], fill=color)
    draw.arc(pt((45, 31, 71, 65)), 292, 68, fill=color, width=pt(4))
    draw.arc(pt((44, 22, 82, 74)), 292, 68, fill=color, width=pt(4))


ICON_DRAWERS = {
    "spark": icon_spark,
    "settings": icon_settings,
    "send": icon_send,
    "meal": icon_meal,
    "calendar": icon_calendar,
    "recovery": icon_recovery,
    "copy": icon_copy,
    "chevron": icon_chevron,
    "shield": icon_shield,
    "add": icon_add,
    "close": icon_close,
    "edit": icon_edit,
    "check": icon_check,
    "delete": icon_delete,
    "upload": icon_upload,
    "camera": icon_camera,
    "activity": icon_activity,
    "trend": icon_trend,
    "target": icon_target,
    "report": icon_report,
    "bell": icon_bell,
    "workout": icon_dumbbell,
    "profile-card": icon_user_card,
    "water": icon_water,
    "sleep": icon_sleep,
    "steps": icon_steps,
    "protein": icon_protein,
    "calories": icon_calories,
    "breathe": icon_breathe,
    "warning": icon_warning,
    "agent-xiaojian": icon_agent_xiaojian,
    "agent-xiaokang": icon_agent_xiaokang,
    "agent-steward": icon_agent_steward,
    "mic": icon_microphone,
    "speaker": icon_speaker,
}


def generate_interface_icons():
    for name, draw_icon in ICON_DRAWERS.items():
        image, draw = canvas()
        draw_icon(draw, INK)
        save(image, ROOT / "icons" / f"{name}.png", 96)


if __name__ == "__main__":
    if "--shell-only" in sys.argv:
        generate_shell_tabs()
        print("Generated HealthMate navigation shell icons.")
    else:
        generate_tabs()
        generate_shell_tabs()
        generate_interface_icons()
        print("Generated HealthMate tab and interface icons.")
