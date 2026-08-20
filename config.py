#!/usr/bin/env python3
"""
Framefetch configuration.

Keep this file in the same directory as Framefetch_FW13_1.py.
Edit values here to customize colors, thresholds, layout, ports, and watch behavior.
"""
import random
# =============================================================================
# ANSI COLORS
# =============================================================================

RESET = "\033[0m"
GREEN = "\033[32m" 
TEAL = "\033[92m" #i refuse to call  this bright cyan 
ORANGE = "\033[33m"
RED = "\033[31m"
BRIGHT_BLUE = "\033[94m"
BLUE = "\033[34m"  
MAGENTA = "\033[35m"
DIM = "\033[2m"
GOLD = "\033[38;5;220m"
BOLD = "\033[1m"

# =============================================================================
# LAYOUT
# =============================================================================

PORT_WIDTH = 15
RAM_BAR_WIDTH = 19
SSD_BAR_WIDTH = 14
BATTERY_BAR_WIDTH = 20
PERIPHERAL_BATTERY_BAR_WIDTH = 13
LEFT_BOARD_GAP = 1
LOGO_SHIFT_RIGHT = 5

# =============================================================================
# CPU USAGE
# =============================================================================

CPU_WARNING_PERCENT = 50
CPU_CRITICAL_PERCENT = 80
COLOR_CPU_OK = GREEN
COLOR_CPU_WARNING = ORANGE
COLOR_CPU_CRITICAL = RED

# =============================================================================
# CPU TEMPERATURE
# =============================================================================

CPU_TEMP_WARNING = 51
CPU_TEMP_CRITICAL = 71
COLOR_CPU_TEMP_OK = GREEN
COLOR_CPU_TEMP_WARNING = ORANGE
COLOR_CPU_TEMP_CRITICAL = RED

# =============================================================================
# GPU USAGE
# =============================================================================

GPU_WARNING_PERCENT = 50
GPU_CRITICAL_PERCENT = 80
COLOR_GPU_OK = GREEN
COLOR_GPU_WARNING = ORANGE
COLOR_GPU_CRITICAL = RED

# =============================================================================
# RAM USAGE
# =============================================================================

RAM_WARNING_PERCENT = 65
RAM_CRITICAL_PERCENT = 80
COLOR_RAM_OK = GREEN
COLOR_RAM_WARNING = ORANGE
COLOR_RAM_CRITICAL = RED
COLOR_RAM_MODULE_SIZE = CYAN
COLOR_RAM_MODULE_SPEED = CYAN

# =============================================================================
# SSD / STORAGE USAGE
# =============================================================================

SSD_WARNING_PERCENT = 50
SSD_CRITICAL_PERCENT = 80
COLOR_SSD_OK = GREEN
COLOR_SSD_WARNING = ORANGE
COLOR_SSD_CRITICAL = RED

# =============================================================================
# BATTERY
# =============================================================================

# Battery status works in the opposite direction: lower percentages are worse.
BATTERY_CRITICAL_PERCENT = 20
BATTERY_WARNING_PERCENT = 50
COLOR_BATTERY_OK = GREEN
COLOR_BATTERY_WARNING = ORANGE
COLOR_BATTERY_CRITICAL = RED

# =============================================================================
# FAN
# =============================================================================

FAN_MAX_RPM_FALLBACK = 6800
FAN_WARNING_PERCENT = 70
FAN_CRITICAL_PERCENT = 90
COLOR_FAN_OK = GREEN
COLOR_FAN_WARNING = ORANGE
COLOR_FAN_CRITICAL = RED

# =============================================================================
# CARD / FUNCTION COLORS
# =============================================================================

COLOR_WIFI = CYAN
COLOR_BLUETOOTH = CYAN
COLOR_ETHERNET = MAGENTA
COLOR_DISPLAYPORT = MAGENTA
COLOR_POWER_DELIVERY = GOLD

# =============================================================================
# LOGO COLOR
# =============================================================================

# Logo selection is iconfigurable now. it was also before i just changed the comment
LOGO_COLOR = TEAL #random.choice([color1, color2, so on]) #if you want it randomized, i personally use teal for now

# "static" -> always use LOGO_COLOR in --watch
# "cycle"  -> rotate through WATCH_LOGO_COLORS in --watch
WATCH_LOGO_COLOR_MODE = "cycle"
WATCH_LOGO_COLORS = [BLUE, ORANGE, GOLD, RED, GREEN, TEAL, BRIGHT_BLUE, MAGENTA] #added the other color in the rotation


# =============================================================================
# FRAMEWORK PORTS
# =============================================================================

# Edit these while the physical USB-port detector is not implemented.
# Each line is rendered inside the matching port box.
# Leave an empty list for an empty port.
PORT_OVERRIDES: dict[int, list[str]] = {
    1: [],
    2: [],
    3: [],
    4: [],
}

PHYSICAL_PORT_TYPE = {
    1: "USB4 · 40 Gb/s",
    2: "USB 3.2 Gen2",
    3: "USB4 · 40 Gb/s",
    4: "USB 3.2 Gen2",
}

# =============================================================================
# LOGO
# =============================================================================



FW_LOGO = [
    "       .%%+ .  =%%..   ",
    "     %%%%%%%%%%%%%%%%.  ",
    "     %%%%%%... %%%%%%  ",
    "    .%%%          %%%.. ",
    "  %%%%+.           =%%%@. ",
    " %%%%%              %%%%%. ",
    " @%%%%             .%%%%@. ",
    "  %%%%=           -%%%%. ",
    "     %%@.        .@%%. ",
    "     %%%%%*....+%%%%%  ",
    "     %%%%%%%%%%%%%%%%  ",
    "      .-%%%    #%@-    ",
]
ARCH_LOGO = [
    "              ++",
    "             +###",
    "            ######",
    "            .######",
    "          ##########",
    "         ############",
    "        ##############",
    "       ######    ######",
    "      ######      ######",
    "     #######      ####..#",
    "    ########      #######",
    "   ####                ####",
    "  #                        +",
]


CACHYOS_LOGO = [
    "      #+############",
    "     +##+++########    ##",
    "    ++###+++######",
    "   ++++##.",
    " .+++###            +##",
    "-+#####             ###",
    ".++++##                    #",
    "  ######                 #####",
    "   ####+#                 ###",
    "    ###++###+++++++++++",
    "     ##+#######+++++++",
    "      ############+++",
]


UBUNTU_LOGO = [
    "                ###+",
    "         ###########",
    "      ##  #####. +",
    "     ####.      #####",
    "    ####         .###",
    "##### ##          #+..",
    "-#### ##          ####",
    "    +###.        ####",
    "     ####      #####",
    "       #  ###### -",
    "          ##### ####",
    "                ####",
]


LOGO=random.choice([FW_LOGO,CACHYOS_LOGO]) #i'm on cachy so now i use this

# Framework physical slot order for the current AI 300 board.
# DMI enumeration observed on this machine is opposite to the physical drawing.
FRAMEWORK_SODIMM_DMI_ORDER = (1, 0)
