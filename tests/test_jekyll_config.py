"""
测试 Jekyll _config.yml 配置，确保包含 Jinja2 模板语法的目录被正确排除，
防止 GitHub Pages 构建时出现 Liquid 语法错误。

问题背景：prompts/ 目录下的 .md 模板文件使用了 Jinja2 专有标签（如 {% elif %}），
Jekyll 的 Liquid 引擎不支持该标签，会导致 GitHub Pages 构建报错：
  Liquid Exception: Liquid syntax error (line N): Unknown tag 'elif'
"""

import os
import re

import pytest
import yaml

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(REPO_ROOT, "_config.yml")

# 包含 Jinja2 专有语法、必须被 Jekyll 排除的目录
REQUIRED_EXCLUDES = ["prompts/"]

# Jinja2 专有标签（Liquid 不支持），扫描 prompts/ 目录时用于检测
JINJA2_ONLY_TAGS = re.compile(r"\{%-?\s*(elif|macro|endmacro|call|endcall|filter|endfilter|set|endset|do|namespace)\b")


class TestJekyllConfig:
    """验证 _config.yml 正确排除含 Jinja2 语法的目录"""

    def test_config_file_exists(self):
        """_config.yml 必须存在"""
        assert os.path.isfile(CONFIG_PATH), (
            "_config.yml 不存在，GitHub Pages 构建时 Jekyll 会处理所有 .md 文件，"
            "导致 prompts/ 下的 Jinja2 模板报 Liquid 语法错误"
        )

    def test_config_is_valid_yaml(self):
        """_config.yml 必须是合法的 YAML"""
        with open(CONFIG_PATH, encoding="utf-8") as f:
            content = yaml.safe_load(f)
        assert content is not None, "_config.yml 内容为空"

    def test_exclude_list_exists(self):
        """_config.yml 必须包含 exclude 字段"""
        with open(CONFIG_PATH, encoding="utf-8") as f:
            config = yaml.safe_load(f)
        assert "exclude" in config, "_config.yml 缺少 exclude 字段"
        assert isinstance(config["exclude"], list), "exclude 字段必须是列表"

    @pytest.mark.parametrize("directory", REQUIRED_EXCLUDES)
    def test_required_directories_are_excluded(self, directory):
        """prompts/ 等含 Jinja2 语法的目录必须在 exclude 列表中"""
        with open(CONFIG_PATH, encoding="utf-8") as f:
            config = yaml.safe_load(f)
        excludes = config.get("exclude", [])
        assert directory in excludes, (
            f"'{directory}' 未在 _config.yml 的 exclude 列表中，"
            f"Jekyll 会尝试渲染其中的 .md 文件导致 Liquid 语法报错。"
            f"当前 exclude 列表: {excludes}"
        )


class TestPromptsJinja2Syntax:
    """验证 prompts/ 目录中确实含有需要被排除的 Jinja2 专有标签"""

    def _collect_prompt_md_files(self):
        prompts_dir = os.path.join(REPO_ROOT, "prompts")
        if not os.path.isdir(prompts_dir):
            return []
        result = []
        for root, _, files in os.walk(prompts_dir):
            for fname in files:
                if fname.endswith(".md"):
                    result.append(os.path.join(root, fname))
        return result

    def test_jinja2_tags_exist_in_prompts(self):
        """确认 prompts/ 中存在 Jinja2 专有标签（如 {% elif %}），排除是必要的"""
        md_files = self._collect_prompt_md_files()
        assert md_files, "prompts/ 目录下没有找到任何 .md 文件"

        found = []
        for fpath in md_files:
            with open(fpath, encoding="utf-8") as f:
                for lineno, line in enumerate(f, start=1):
                    if JINJA2_ONLY_TAGS.search(line):
                        rel = os.path.relpath(fpath, REPO_ROOT)
                        found.append(f"{rel}:{lineno}: {line.rstrip()}")

        assert found, (
            "prompts/ 中未检测到任何 Jinja2 专有标签，请确认此测试仍有意义"
        )

    def test_no_jinja2_tags_outside_excluded_dirs(self):
        """website/ 等未被排除的目录中不应含 Jinja2 专有标签，否则也会导致构建失败"""
        with open(CONFIG_PATH, encoding="utf-8") as f:
            config = yaml.safe_load(f)
        excludes = set(config.get("exclude", []))

        # 只检查 website/ 目录（GitHub Pages 实际渲染目标）
        website_dir = os.path.join(REPO_ROOT, "website")
        if not os.path.isdir(website_dir):
            pytest.skip("website/ 目录不存在，跳过此检查")

        violations = []
        for root, _, files in os.walk(website_dir):
            rel_root = os.path.relpath(root, REPO_ROOT)
            # 跳过已排除的目录
            if any(rel_root.startswith(ex.rstrip("/")) for ex in excludes):
                continue
            for fname in files:
                if not fname.endswith(".md"):
                    continue
                fpath = os.path.join(root, fname)
                with open(fpath, encoding="utf-8") as f:
                    for lineno, line in enumerate(f, start=1):
                        if JINJA2_ONLY_TAGS.search(line):
                            rel = os.path.relpath(fpath, REPO_ROOT)
                            violations.append(f"{rel}:{lineno}: {line.rstrip()}")

        assert not violations, (
            "以下文件位于未排除的目录中，但包含 Jinja2 专有标签，会导致 Jekyll 构建失败：\n"
            + "\n".join(violations)
        )
