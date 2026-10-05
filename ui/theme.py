"""
Central theme / design tokens for WA Forensicator.

A calm, premium dark forensic look: near-black surfaces with clear elevation
steps, one emerald accent, restrained borders. Old token names are kept as
aliases at the bottom so existing tab code keeps working.
"""

# ---- Surfaces (dark -> elevated) -----------------------------------------
BG_APP       = "#0A0E17"   # window background
BG_SIDEBAR   = "#0D1220"   # left navigation rail
BG_HEADER    = "#0E1422"   # top bar
BG_PANEL     = "#121826"   # main content surface
BG_CARD      = "#161E2E"   # elevated card / stat tile
BG_INPUT     = "#0E1420"   # inset entry / console

# ---- Borders --------------------------------------------------------------
BORDER        = "#232D40"  # card / panel outline
BORDER_SUBTLE = "#1A2231"  # faint divider

# ---- Accent + status ------------------------------------------------------
ACCENT        = "#10B981"  # emerald — the single brand accent
ACCENT_DIM    = "#0E9E73"
ACCENT_SOFT   = "#15271F"  # active nav background tint
ACCENT_CYAN   = "#22D3EE"
ACCENT_BLUE   = "#60A5FA"
ACCENT_PURPLE = "#A78BFA"
ACCENT_RED    = "#F87171"
ACCENT_YELLOW = "#FBBF24"

# ---- Navigation -----------------------------------------------------------
NAV_HOVER     = "#182133"
NAV_ACTIVE    = "#15241D"

# ---- Text -----------------------------------------------------------------
TEXT_PRIMARY   = "#F3F6FB"
TEXT_SECONDARY = "#C4CDDC"
TEXT_MUTED     = "#8592A8"
TEXT_FAINT     = "#5A6680"

# ---- Chat bubbles ---------------------------------------------------------
BUBBLE_ME     = "#12402E"  # outgoing (deep emerald)
BUBBLE_ME_TX  = "#DFF7EC"
BUBBLE_THEM   = "#1B2334"  # incoming
BUBBLE_THEM_TX= "#E7ECF5"

# ---- Buttons --------------------------------------------------------------
BTN_PRIMARY_BG    = "#0E9E73"
BTN_PRIMARY_HOVER = "#10B981"
BTN_SECONDARY_BG    = "#1B2437"
BTN_SECONDARY_HOVER = "#273246"

# ---- Status ---------------------------------------------------------------
STATUS_OK      = "#10B981"
STATUS_WARN    = "#FBBF24"
STATUS_ERR     = "#F87171"
STATUS_OFFLINE = "#6B7280"

# ---- Typography -----------------------------------------------------------
_UI   = "Segoe UI"
_MONO = "Consolas"

FONT_H1          = (_UI, 19, "bold")
FONT_SUB         = (_UI, 11)
FONT_SECTION     = (_UI, 10, "bold")   # sidebar group captions
FONT_NAV         = (_UI, 13)
FONT_LABEL       = (_UI, 12, "bold")
FONT_BODY        = (_UI, 12)
FONT_SMALL       = (_UI, 10)
FONT_PILL        = (_UI, 10, "bold")
FONT_MONO        = (_MONO, 11)
FONT_MONO_SMALL  = (_MONO, 10)
FONT_STAT_VALUE  = (_UI, 24, "bold")
FONT_STAT_LABEL  = (_UI, 10)
FONT_BUBBLE      = (_UI, 12)
FONT_BUBBLE_META = (_UI, 9)

# ---- Spacing scale --------------------------------------------------------
PAD_XS, PAD_SM, PAD_MD, PAD_LG, PAD_XL = 4, 8, 12, 16, 24

# ===========================================================================
# Backwards-compatible aliases (old names used across ui/tabs/*).
# ===========================================================================
FONT_FAMILY    = _MONO
FONT_FAMILY_UI = _UI
FONT_HEADER    = FONT_H1
FONT_SUBHEADER = FONT_SUB

BG_PRIMARY   = BG_APP
BG_PANEL_ALT = BG_CARD

BORDER_GREEN        = BORDER_SUBTLE
BORDER_GREEN_BRIGHT = ACCENT
BORDER_ACCENT       = BORDER

ACCENT_GREEN = ACCENT
