"""Reject a native palette shortcut that collides with pinned Zed defaults."""
from pathlib import Path
import argparse
import re


def palette_shortcut(patch_text: str) -> str:
    matches = re.findall(
        r'KeyBinding::new\("([^"]+)",\s*ToggleMzedPalette,\s*None\)', patch_text
    )
    if len(matches) != 1:
        raise ValueError(f'expected exactly one ToggleMzedPalette binding, found {len(matches)}')
    return matches[0]


def ensure_palette_binding_reloads(patch_text: str) -> None:
    if 'fn bind_mzed_palette_key(cx: &mut App)' not in patch_text:
        raise ValueError('MZed palette binding helper is missing')

    start = patch_text.find('diff --git a/crates/zed/src/zed.rs b/crates/zed/src/zed.rs')
    if start < 0:
        raise ValueError('zed.rs patch section is missing')
    end = patch_text.find('\ndiff --git ', start + 1)
    section = patch_text[start:end if end >= 0 else None]

    hunk_headers = []
    hunk_texts = []
    added_calls = []
    for hunk in re.split(r'(?=^@@ )', section, flags=re.MULTILINE)[1:]:
        hunk_headers.append(hunk.splitlines()[0])
        lines = hunk.splitlines()[1:]
        new_lines = []
        for line in lines:
            if line.startswith('+') and not line.startswith('+++'):
                content = line[1:]
                new_lines.append(content)
                if content.strip() == 'bind_mzed_palette_key(cx);':
                    added_calls.append(len(hunk_texts))
            elif line.startswith(' ') or line.startswith('\\'):
                new_lines.append(line[1:] if line.startswith(' ') else line)
        hunk_texts.append('\n'.join(new_lines))

    if len(added_calls) != 2:
        raise ValueError('MZed palette binding must appear in both default-keymap paths only')

    helper_hunk = next(
        (hunk for hunk in hunk_texts if 'fn bind_mzed_palette_key(cx: &mut App)' in hunk),
        None,
    )
    if helper_hunk is None or 'MZED_NATIVE_PALETTE' not in helper_hunk:
        raise ValueError('MZed palette binding must remain gated by MZED_NATIVE_PALETTE=1')

    first_index, second_index = added_calls
    first_hunk = hunk_texts[first_index]
    second_hunk = hunk_texts[second_index]
    if 'pub fn load_default_keymap(cx: &mut App)' not in hunk_headers[second_index]:
        raise ValueError('MZed palette binding must be inside load_default_keymap')
    if 'pub fn load_default_keymap(cx: &mut App)' not in first_hunk:
        raise ValueError('MZed palette binding must be inside load_default_keymap')

    none_path = re.search(
        r'if base_keymap == BaseKeymap::None \{(?:(?!\n\s*\}).)*?'
        r'#\[cfg\(target_os = "linux"\)\]\s*'
        r'bind_mzed_palette_key\(cx\);(?:(?!\n\s*\}).)*?\breturn;',
        first_hunk,
        re.DOTALL,
    )
    override_at = second_hunk.find('SPECIFIC_OVERRIDES_KEYMAP_PATH')
    binding_at = second_hunk.find('bind_mzed_palette_key(cx);')
    defaults_path = re.search(
        r'\.unwrap\(\),\s*\);(?:(?!\n\s*\}).)*?'
        r'#\[cfg\(target_os = "linux"\)\]\s*'
        r'bind_mzed_palette_key\(cx\);',
        second_hunk,
        re.DOTALL,
    ) and 0 <= override_at < binding_at
    if not none_path or not defaults_path:
        raise ValueError('MZed palette binding must be re-applied in both default-keymap paths')


def ensure_no_default_linux_collision(source: Path, patch_text: str) -> str:
    shortcut = palette_shortcut(patch_text)
    ensure_palette_binding_reloads(patch_text)
    keymap = source / 'assets/keymaps/default-linux.json'
    if not keymap.is_file():
        raise FileNotFoundError(f'pinned Linux keymap is missing: {keymap}')
    if re.search(r'"' + re.escape(shortcut) + r'"\s*:', keymap.read_text()):
        raise ValueError(f'MZed palette shortcut collides with the pinned Linux default: {shortcut}')
    return shortcut


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    args = parser.parse_args()
    patch = Path(__file__).resolve().parents[1] / 'patches/native-island.patch'
    print(ensure_no_default_linux_collision(args.source.resolve(), patch.read_text()))
