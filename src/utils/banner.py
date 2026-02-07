"""
启动横幅生成器
使用 Rich 库动态生成居中对齐的横幅
"""
from rich.console import Console
from rich.panel import Panel
from rich.text import Text
from typing import Optional


def create_startup_banner(
    title: str = "Quant Flow Trading Bot",
    subtitle: str = "Multi-Agent AI Trading System",
    platform: str = "Hyperliquid",
    version: Optional[str] = None,
    console: Optional[Console] = None
) -> Panel:
    """
    创建居中对齐的启动横幅

    Args:
        title: 主标题
        subtitle: 副标题
        platform: 交易平台名称
        version: 版本号（可选）
        console: Rich Console 实例（可选）

    Returns:
        Rich Panel 对象
    """
    if console is None:
        console = Console()

    # 创建标题文本
    title_text = Text()
    title_text.append("🤖 ", style="bold yellow")
    title_text.append(title, style="bold cyan")
    title_text.append(" 🤖", style="bold yellow")

    # 创建内容 - 使用 Text 对象并设置 justify
    content = Text(justify="center")
    content.append(f"\n{subtitle}\n", style="bold white")
    content.append(f"Platform: {platform}\n", style="cyan")

    if version:
        content.append(f"Version: {version}\n", style="dim cyan")

    content.append("\n")

    # 创建面板
    panel = Panel(
        content,
        title=title_text,
        border_style="bold cyan",
        padding=(1, 2),
        expand=False
    )

    return panel


def print_startup_banner(
    config: Optional[any] = None,
    console: Optional[Console] = None
):
    """
    打印启动横幅和配置信息

    Args:
        config: 配置对象
        console: Rich Console 实例
    """
    if console is None:
        console = Console()

    # 打印横幅
    banner = create_startup_banner(
        title="Quant Flow Trading Bot",
        subtitle="Multi-Agent AI Trading System",
        platform="Hyperliquid",
        version="2.0.0",
        console=console
    )

    console.print("\n")
    console.print(banner, justify="center")
    console.print("\n")

    # 打印配置信息（如果提供）
    if config:
        console.print(config, style="cyan")
        console.print("\n")


def print_section_separator(
    console: Optional[Console] = None,
    char: str = "═",
    length: int = 80,
    style: str = "dim cyan"
):
    """
    打印章节分隔线

    Args:
        console: Rich Console 实例
        char: 分隔符字符
        length: 分隔线长度
        style: Rich 样式
    """
    if console is None:
        console = Console()

    console.print(char * length, style=style)


def print_box_message(
    message: str,
    title: str = "",
    console: Optional[Console] = None,
    border_style: str = "cyan",
    padding: tuple = (1, 2)
):
    """
    打印带边框的消息

    Args:
        message: 消息内容
        title: 标题
        console: Rich Console 实例
        border_style: 边框样式
        padding: 内边距
    """
    if console is None:
        console = Console()

    panel = Panel(
        message,
        title=title,
        border_style=border_style,
        padding=padding
    )

    console.print(panel)
