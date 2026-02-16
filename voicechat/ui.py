from __future__ import annotations

from dataclasses import dataclass

try:
    from colorama import Fore, Style, init as colorama_init
except Exception:
    Fore = Style = None

    def colorama_init(*args: object, **kwargs: object) -> None:
        return None


@dataclass(frozen=True)
class _Palette:
    blue: str
    cyan: str
    green: str
    yellow: str
    red: str
    magenta: str
    reset: str
    bright: str


def _build_palette() -> _Palette:
    if Fore is None or Style is None:
        return _Palette("", "", "", "", "", "", "", "")
    return _Palette(
        blue=Fore.BLUE,
        cyan=Fore.CYAN,
        green=Fore.GREEN,
        yellow=Fore.YELLOW,
        red=Fore.RED,
        magenta=Fore.MAGENTA,
        reset=Style.RESET_ALL,
        bright=Style.BRIGHT,
    )


P = _build_palette()


def init_console() -> None:
    colorama_init(autoreset=True)


def _c(text: str, color: str = "", bright: bool = False) -> str:
    prefix = ""
    if bright:
        prefix += P.bright
    if color:
        prefix += color
    if not prefix:
        return text
    return f"{prefix}{text}{P.reset}"


def tag(name: str, color: str) -> str:
    return _c(f"[{name}]", color=color, bright=True)


def banner(title: str, subtitle: str = "") -> None:
    print()
    print(_c(f"=== {title} ===", color=P.magenta, bright=True))
    if subtitle:
        print(_c(subtitle, color=P.cyan))


def info(message: str) -> None:
    print(f"{tag('INFO', P.cyan)} {message}")


def ok(message: str) -> None:
    print(f"{tag('OK', P.green)} {message}")


def warn(message: str) -> None:
    print(f"{tag('WARN', P.yellow)} {message}")


def error(message: str) -> None:
    print(f"{tag('ERROR', P.red)} {message}")


def chat(sender: str, message: str) -> None:
    print(f"\n{tag('CHAT', P.blue)} {_c(sender, color=P.blue, bright=True)}: {message}")


def section(title: str) -> None:
    print()
    print(_c(title, color=P.blue, bright=True))


def prompt_text(label: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    prompt = f"{_c(label, color=P.yellow, bright=True)}{_c(suffix, color=P.cyan)}: "
    try:
        entered = input(prompt).strip()
    except EOFError:
        print(f"{_c(label + suffix, color=P.yellow)}: <auto>")
        return default
    return entered or default
