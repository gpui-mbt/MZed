# MZed：動くZedを保つMoonBit部分置換PoC

Status: open — 設計課題。PoCの成功条件を定義し、完成エディタや全面移植を約束しない。
Priority: P1
Decision date: 2026-10-15
Parent: [ecosystem方針・共通マージ基準](https://github.com/gpui-mbt/gpui.mbt/pull/82)

## 1. 役割

MZedはgpui.mbtの**技術的な実証アプリ（PoC）**。動作するRust Zedを残し、小さな任意のUI領域をMoonBit/gpui.mbtへ置き換え、同じwindow内で描画・入力・focus・lifecycleを成立させられるか調べる。移植した行数ではなく、利用可能な境界と再現可能な証拠を成果にする。

dshは別のhobby consumerであり、MZedをdshの起動に必須化しない。MZedの全機能完成もgpui.mbtの各改善PRの前提にしない。ただしMZedを対応consumerとして主張するAPI変更では該当する互換テストを必要とする。

## 2. 現状

[README](../../README.md)に固定Zed baseline、独立したopt-in native island、OS別qualificationと限界がある。既存islandはstatus bar内のcopied-scene領域であり、baseline laneとderived-source laneは別。ABIがlinkできることだけではsame-window動作の証明にならない。

[Linux baseline](../../docs/linux-baseline.md)、[native island](../../docs/native-island.md)、[macOS](../../docs/macos.md)、[Windows](../../docs/windows-validation.md)の既存手順と未検証範囲を引き継ぐ。過去の特定DPI・monitor条件の成功を他条件へ広げない。

## 3. 対象外と依存境界

1. 全Zed機能のMoonBit化、独立した完成エディタのリリース、独自UI engineの追加は対象外。
2. upstream Zedはread-only。upstreamへissue・PR・comment・pushしない。派生変更とpin・attributionは本repoに保持し、汎用engine修正はgpui.mbtへ分離する。
3. Rust Zedを使うPoCなのでRustの存在自体は違反ではない。一方、ここで必要なRust依存をgpui.mbtの通常runtimeへ持ち込まない。
4. Yami-kumoを使う領域ではUXの契約を再利用するが、Yami-kumo全componentの実装をPoC成立の必須条件にしない。
5. gpui-conformanceはgpui.rsとの差分を調べる。MZedのsame-window実動作はMZedが証拠を所有し、vlmkitは安全に取得可能な外部観測を補助する。turtles/hotpathは開発専用。

## 4. 実装単位

1. **PoC台帳**：未変更baselineとisland有効laneを並べ、Zed／gpui.mbt／MZedのSHA・toolchain・OS/arch・DPI・fixture・実行証拠を記録する。macOSはApple Siliconのみ、Linuxは実profileを明示し、Windowsの既存laneを保持する。
2. **最小縦切りの固定**：現在のstatus bar islandを最初の対象とし、create → render → 操作 → resize/focus変化 → destroyを1つのcontractで追う。領域追加はこれが成立した後の別課題にする。
3. **退避可能な境界**：copied-scene／ABIの所有権、寿命、thread、失敗時の挙動を明示する。island無効ではbaselineが動作し、エラー時に黙って成功・互換にしない。既存editorの入力・保存・終了を壊さない。
4. **問題の還元**：汎用問題は独立fixtureへ縮約し、engineはgpui.mbt、比較はgpui-conformance、UXはYami-kumo、観測はvlmkitへ関連付ける。本repoに同じ修正を重複保持しない。

## 5. PoCの受入条件

| 対象 | 必須の証拠 |
| --- | --- |
| baseline | 専用の一時fixtureで起動・open/edit/save/closeを確認し、ユーザーファイルを使わない |
| opt-in island | 同じeditor window内にMoonBit領域があることを特定し、frameと操作を対応付ける |
| input/focus | 領域境界、focus喪失、resize等で誤配信や古い座標の操作を起こさない |
| lifecycle | create/destroy・無効化・失敗後に所有resourceを終了し、baselineを破壊しない |
| rollback | island無効で同じ固定baselineを再現できる |
| negative control | 描画・入力・ABI等の意図した不整合を検出し、build成功だけで通さない |
| portability | 成功したOS/DPI/profileのみをqualifiedとし、他は未実行／未検証を表示する |

nativeテストはowned fixtureに限定する。ユーザーの作業windowをactivate・入力・killしない。環境不足でnative実行できない場合はbuild-only／未検証と記録し、PoC成功と呼ばない。

## 6. 検証方法

既存harness testは`python3 -m unittest discover -s tests -v`。nativeは上記OS別guideの実コマンドを使い、exact-sourceの結果とartifactを保存する。テスト対象、コマンド、SHA、PASS/FAIL/未実行、限界を記録する。今回のMarkdown追加ではこれらは未実行。

## 7. マージ基準・完了条件

共通`ecosystem-merge-v1`に従う。**最新HEADの必要CIがすべてcompleted/success AND 同じHEADを実際にレビューした`gpt-6.1-sol/xhigh`のApprove相当**。モデル・effort・対象SHA・判定・指摘処置・証拠URLをGitHubへ残す。P0/P1、必要checkのskip、CI未実行、古い承認を合格にしない。HEAD更新後は再検証・再レビューし、既存保護を維持する。

1. [ ] baselineとislandのlane・証拠・未検証範囲を分けたPoC台帳がある。
2. [ ] 実Native profileで最小縦切りとisland無効時のbaselineを確認する。
3. [ ] 境界／lifecycleの反例を回帰テストにする。
4. [ ] 見つけた汎用課題を担当repoへ還元し、全面移植を完了条件にしない。
