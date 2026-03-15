"""
启动横幅生成器
使用 Rich 库动态生成居中对齐的横幅
"""

from rich.console import Console
from rich.panel import Panel
from rich.text import Text


def create_startup_banner(
    title: str = "Quant Flow Trading Bot",
    subtitle: str = "Multi-Agent AI Trading System",
    platform: str = "Hyperliquid",
    version: str | None = None,
    console: Console | None = None,
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
    panel = Panel(content, title=title_text, border_style="bold cyan", padding=(1, 2), expand=False)

    return panel


def print_startup_banner(config: object | None = None, console: Console | None = None):
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
        console=console,
    )

    console.print("\n")
    console.print(banner, justify="center")
    console.print("\n")

    # 打印配置信息（如果提供）
    if config:
        console.print(config, style="cyan")
        console.print("\n")
