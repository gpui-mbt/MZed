from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from check_palette_shortcut import ensure_no_default_linux_collision, ensure_palette_binding_reloads, palette_shortcut


def zed_patch(shortcut='ctrl-alt-shift-z', *, base_none=True, defaults=True,
              init_call=False, gated=True, early_defaults_binding=False):
    none_path = '''@@ -2375,6 +2375,8 @@ fn reload_keymaps(cx: &mut App) {
 pub fn load_default_keymap(cx: &mut App) {
     let base_keymap = *BaseKeymap::get_global(cx);
     if base_keymap == BaseKeymap::None {
+        #[cfg(target_os = "linux")]
+        bind_mzed_palette_key(cx);
         return;
'''
    if not base_none:
        none_path = none_path.replace('+        bind_mzed_palette_key(cx);\n', '')

    binding = '+    #[cfg(target_os = "linux")]\n+    bind_mzed_palette_key(cx);\n'
    default_prefix = '''@@ -2410,6 +2410,10 @@ pub fn load_default_keymap(cx: &mut App) {
         KeymapFile::load_asset(DEFAULT_KEYMAP_PATH, None, cx)
         .unwrap(),
     );
'''
    if early_defaults_binding and defaults:
        default_path = default_prefix + binding + '''         KeymapFile::load_asset(
             SPECIFIC_OVERRIDES_KEYMAP_PATH,
             Some(KeybindSource::Default),
             cx,
         )
         .unwrap(),
     );
'''
    else:
        default_path = default_prefix + '''         KeymapFile::load_asset(
             SPECIFIC_OVERRIDES_KEYMAP_PATH,
             Some(KeybindSource::Default),
             cx,
         )
         .unwrap(),
     );
'''
        if defaults:
            default_path += '+\n+    // Reapply after defaults; reload_keymaps adds user bindings afterward.\n'
            default_path += binding
    helper = f'''@@ -341,0 +342,7 @@
+fn bind_mzed_palette_key(cx: &mut App) {{
'''
    if gated:
        helper += '+    if std::env::var("MZED_NATIVE_PALETTE").as_deref() == Ok("1") {\n'
        helper += f'+        cx.bind_keys([KeyBinding::new("{shortcut}", ToggleMzedPalette, None)]);\n'
        helper += '+    }\n'
    else:
        helper += f'+    cx.bind_keys([KeyBinding::new("{shortcut}", ToggleMzedPalette, None)]);\n'
    helper += '+}\n'
    init_hunk = '''@@ -201,1 +201,2 @@ pub fn init(cx: &mut App) {
'''
    if init_call:
        init_hunk += '+    bind_mzed_palette_key(cx);\n'
    return 'diff --git a/crates/zed/src/zed.rs b/crates/zed/src/zed.rs\n' + init_hunk + helper + none_path + default_path


class PaletteShortcutTests(unittest.TestCase):
    def test_shortcut_is_extracted_and_checked_against_pinned_keymap(self):
        patch = zed_patch()
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)
            keymap = source / 'assets/keymaps/default-linux.json'
            keymap.parent.mkdir(parents=True)
            keymap.write_text('{ "ctrl-alt-shift-p": "dev::ToggleFpsOverlay" }\n')
            self.assertEqual(ensure_no_default_linux_collision(source, patch), 'ctrl-alt-shift-z')

    def test_default_linux_collision_fails_closed(self):
        patch = zed_patch(shortcut='ctrl-alt-shift-p')
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)
            keymap = source / 'assets/keymaps/default-linux.json'
            keymap.parent.mkdir(parents=True)
            keymap.write_text('{ "ctrl-alt-shift-p": "dev::ToggleFpsOverlay" }\n')
            with self.assertRaisesRegex(ValueError, 'collides'):
                ensure_no_default_linux_collision(source, patch)

    def test_multiple_or_missing_bindings_are_rejected(self):
        with self.assertRaisesRegex(ValueError, 'exactly one'):
            palette_shortcut('ToggleMzedPalette')
        with self.assertRaisesRegex(ValueError, 'exactly one'):
            palette_shortcut('KeyBinding::new("a", ToggleMzedPalette, None); KeyBinding::new("b", ToggleMzedPalette, None);')

    def test_both_default_keymap_paths_reapply_the_binding(self):
        ensure_palette_binding_reloads(zed_patch())

    def test_missing_default_or_no_keymap_path_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'both default-keymap paths'):
            ensure_palette_binding_reloads(zed_patch(defaults=False))
        with self.assertRaisesRegex(ValueError, 'both default-keymap paths'):
            ensure_palette_binding_reloads(zed_patch(base_none=False))

    def test_redundant_init_registration_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'only'):
            ensure_palette_binding_reloads(zed_patch(init_call=True))

    def test_environment_gate_is_required(self):
        with self.assertRaisesRegex(ValueError, 'MZED_NATIVE_PALETTE=1'):
            ensure_palette_binding_reloads(zed_patch(gated=False))

    def test_binding_must_follow_specific_overrides(self):
        with self.assertRaisesRegex(ValueError, 'both default-keymap paths'):
            ensure_palette_binding_reloads(zed_patch(early_defaults_binding=True))


if __name__ == '__main__':
    unittest.main()
