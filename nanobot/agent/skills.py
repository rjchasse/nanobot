"""Skills loader for agent capabilities."""

import json
import os
import re
import shutil
from pathlib import Path

# Default builtin skills directory (relative to this file)
BUILTIN_SKILLS_DIR = Path(__file__).parent.parent / "skills"

# Max characters of context to inject per skill (~5k tokens)
MAX_CONTEXT_CHARS = 20_000


class SkillsLoader:
    """
    Loader for agent skills.
    
    Skills are markdown files (SKILL.md) that teach the agent how to use
    specific tools or perform certain tasks.
    """
    
    def __init__(self, workspace: Path, builtin_skills_dir: Path | None = None):
        self.workspace = workspace
        self.workspace_skills = workspace / "skills"
        self.builtin_skills = builtin_skills_dir or BUILTIN_SKILLS_DIR
    
    def list_skills(self, filter_unavailable: bool = True) -> list[dict[str, str]]:
        """
        List all available skills.
        
        Args:
            filter_unavailable: If True, filter out skills with unmet requirements.
        
        Returns:
            List of skill info dicts with 'name', 'path', 'source'.
        """
        skills = []
        
        # Workspace skills (highest priority) — supports both flat and nested layouts
        if self.workspace_skills.exists():
            for skill_file in sorted(self.workspace_skills.rglob("SKILL.md")):
                skill_dir = skill_file.parent
                skills.append({"name": skill_dir.name, "path": str(skill_file), "source": "workspace"})
        
        # Built-in skills
        if self.builtin_skills and self.builtin_skills.exists():
            for skill_dir in self.builtin_skills.iterdir():
                if skill_dir.is_dir():
                    skill_file = skill_dir / "SKILL.md"
                    if skill_file.exists() and not any(s["name"] == skill_dir.name for s in skills):
                        skills.append({"name": skill_dir.name, "path": str(skill_file), "source": "builtin"})
        
        # Filter by requirements
        if filter_unavailable:
            return [s for s in skills if self._check_requirements(self._get_skill_meta(s["name"]))]
        return skills
    
    def _read_skill_file(self, name: str) -> str | None:
        """Read raw SKILL.md content without context resolution."""
        # Flat layout: skills/name/SKILL.md
        workspace_skill = self.workspace_skills / name / "SKILL.md"
        if workspace_skill.exists():
            return workspace_skill.read_text(encoding="utf-8")
        # Nested layout: skills/category/name/SKILL.md
        if self.workspace_skills.exists():
            for match in self.workspace_skills.glob(f"*/{name}/SKILL.md"):
                return match.read_text(encoding="utf-8")
        # Builtin skills (flat only)
        if self.builtin_skills:
            builtin_skill = self.builtin_skills / name / "SKILL.md"
            if builtin_skill.exists():
                return builtin_skill.read_text(encoding="utf-8")
        return None

    def load_skill(self, name: str) -> str | None:
        """
        Load a skill by name, with context files auto-appended.

        Args:
            name: Skill name (directory name).

        Returns:
            Skill content (with context appended) or None if not found.
        """
        content = self._read_skill_file(name)
        if content:
            meta = self.get_skill_metadata(name) or {}
            ctx_str = meta.get("context", "")
            if ctx_str:
                resolved = self._resolve_context(ctx_str)
                if resolved:
                    content += f"\n\n---\n\n## Context\n\n{resolved}"
        return content

    def _resolve_context(self, context_str: str) -> str:
        """Resolve context paths relative to workspace/context/ and return concatenated contents.

        Enforces MAX_CONTEXT_CHARS budget. Files that would exceed the limit are skipped
        and a warning is appended listing what was dropped.
        """
        parts = []
        total = 0
        skipped = []
        base = self.workspace / "context"

        # Collect all (relative_path, file_path) pairs in declaration order
        files: list[tuple[str, Path]] = []
        for raw in context_str.split(","):
            raw = raw.strip()
            if not raw:
                continue
            path = (base / raw).resolve()
            try:
                path.relative_to(base.resolve())
            except ValueError:
                continue
            if path.is_file() and path.suffix == ".md":
                files.append((str(path.relative_to(base)), path))
            elif path.is_dir():
                for md in sorted(path.glob("*.md")):
                    files.append((str(md.relative_to(base)), md))

        for rel, filepath in files:
            content = filepath.read_text(encoding="utf-8")
            chunk = f"### {rel}\n\n{content}"
            if total + len(chunk) > MAX_CONTEXT_CHARS:
                skipped.append(rel)
                continue
            parts.append(chunk)
            total += len(chunk)

        result = "\n\n---\n\n".join(parts)
        if skipped:
            result += f"\n\n---\n\n_Context budget exceeded. Skipped: {', '.join(skipped)}_"
        return result
    
    def load_skills_for_context(self, skill_names: list[str]) -> str:
        """
        Load specific skills for inclusion in agent context.
        
        Args:
            skill_names: List of skill names to load.
        
        Returns:
            Formatted skills content.
        """
        parts = []
        for name in skill_names:
            content = self.load_skill(name)
            if content:
                content = self._strip_frontmatter(content)
                parts.append(f"### Skill: {name}\n\n{content}")
        
        return "\n\n---\n\n".join(parts) if parts else ""
    
    def build_skills_summary(self) -> str:
        """
        Build a summary of all skills (name, description, path, availability).
        
        This is used for progressive loading - the agent can read the full
        skill content using read_file when needed.
        
        Returns:
            XML-formatted skills summary.
        """
        all_skills = self.list_skills(filter_unavailable=False)
        if not all_skills:
            return ""
        
        def escape_xml(s: str) -> str:
            return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        
        lines = ["<skills>"]
        for s in all_skills:
            name = escape_xml(s["name"])
            path = s["path"]
            desc = escape_xml(self._get_skill_description(s["name"]))
            skill_meta = self._get_skill_meta(s["name"])
            available = self._check_requirements(skill_meta)
            
            lines.append(f"  <skill available=\"{str(available).lower()}\">")
            lines.append(f"    <name>{name}</name>")
            lines.append(f"    <description>{desc}</description>")
            lines.append(f"    <location>{path}</location>")
            
            # Show missing requirements for unavailable skills
            if not available:
                missing = self._get_missing_requirements(skill_meta)
                if missing:
                    lines.append(f"    <requires>{escape_xml(missing)}</requires>")
            
            lines.append(f"  </skill>")
        lines.append("</skills>")
        
        return "\n".join(lines)
    
    def _get_missing_requirements(self, skill_meta: dict) -> str:
        """Get a description of missing requirements."""
        missing = []
        requires = skill_meta.get("requires", {})
        for b in requires.get("bins", []):
            if not shutil.which(b):
                missing.append(f"CLI: {b}")
        for env in requires.get("env", []):
            if not os.environ.get(env):
                missing.append(f"ENV: {env}")
        return ", ".join(missing)
    
    def _get_skill_description(self, name: str) -> str:
        """Get the description of a skill from its frontmatter."""
        meta = self.get_skill_metadata(name)
        if meta and meta.get("description"):
            return meta["description"]
        return name  # Fallback to skill name
    
    def _strip_frontmatter(self, content: str) -> str:
        """Remove YAML frontmatter from markdown content."""
        if content.startswith("---"):
            match = re.match(r"^---\n.*?\n---\n", content, re.DOTALL)
            if match:
                return content[match.end():].strip()
        return content
    
    def _parse_nanobot_metadata(self, raw: str) -> dict:
        """Parse skill metadata JSON from frontmatter (supports nanobot and openclaw keys)."""
        try:
            data = json.loads(raw)
            return data.get("nanobot", data.get("openclaw", {})) if isinstance(data, dict) else {}
        except (json.JSONDecodeError, TypeError):
            return {}
    
    def _check_requirements(self, skill_meta: dict) -> bool:
        """Check if skill requirements are met (bins, env vars)."""
        requires = skill_meta.get("requires", {})
        for b in requires.get("bins", []):
            if not shutil.which(b):
                return False
        for env in requires.get("env", []):
            if not os.environ.get(env):
                return False
        return True
    
    def _get_skill_meta(self, name: str) -> dict:
        """Get nanobot metadata for a skill (cached in frontmatter)."""
        meta = self.get_skill_metadata(name) or {}
        return self._parse_nanobot_metadata(meta.get("metadata", ""))
    
    def get_always_skills(self) -> list[str]:
        """Get skills marked as always=true that meet requirements."""
        result = []
        for s in self.list_skills(filter_unavailable=True):
            meta = self.get_skill_metadata(s["name"]) or {}
            skill_meta = self._parse_nanobot_metadata(meta.get("metadata", ""))
            if skill_meta.get("always") or meta.get("always"):
                result.append(s["name"])
        return result
    
    def get_skill_metadata(self, name: str) -> dict | None:
        """
        Get metadata from a skill's frontmatter.
        
        Args:
            name: Skill name.
        
        Returns:
            Metadata dict or None.
        """
        content = self._read_skill_file(name)
        if not content:
            return None

        if content.startswith("---"):
            match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
            if match:
                # Simple YAML parsing
                metadata = {}
                for line in match.group(1).split("\n"):
                    if ":" in line:
                        key, value = line.split(":", 1)
                        metadata[key.strip()] = value.strip().strip('"\'')
                return metadata
        
        return None
