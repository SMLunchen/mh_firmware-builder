# device-ui: Bluetooth pairing PIN is invisible when a custom boot logo fills the screen

**Component:** [`meshtastic/device-ui`](https://github.com/meshtastic/device-ui) —
`source/graphics/TFT/TFTView_320x240.cpp`
**Seen with:** firmware `v2.8.0.47db0e3`, device-ui pinned at
`1c45ebc7433acb8ba3fe96a6f7deca9c43fa54cf`
**Affects:** every board using `TFTView_320x240` — 23 of the 24 `HAS_TFT`
environments in 2.8.0, including `heltec-v4-tft`, `heltec-v4-r8-tft`,
`t-deck-tft`, the Elecrow and mesh-tab families.

---

## Summary

Shipping a custom `branding/logo_<width>x<height>.png` that is taller than half
the display makes the **Bluetooth pairing PIN permanently invisible**. The
device enters programming mode and reports it over serial, but the screen never
shows the six digits, so the node cannot be paired.

The boot screen hides `firmware_label` to make room for a large logo. The
programming-mode branch writes the PIN into that very label — and never clears
the hidden flag.

---

## Reproduction

1. Place a boot logo whose height is greater than half the panel height in
   `branding/logo_<width>x<height>.png`. On a 240×320 Heltec V4 R8 TFT that is
   anything taller than 160 px; a full-screen 240×320 image is the obvious
   choice.
2. Build and flash — the logo shows correctly on boot.
3. Enter programming mode (long-press the boot logo).
4. The screen shows `>> Programming mode <<`, but **no PIN**.

Using a logo of at most half the display height makes the PIN appear again.
That is the workaround we are currently applying.

### Scope across the view classes

All four `TFTView_*` classes derive from `MeshtasticView` directly; there is no
inheritance between them. Only `TFTView_320x240` implements the boot-logo and
programming-mode UI:

| View | Lines | Boot-logo size check | Programming mode | PIN display |
|---|---|---|---|---|
| `TFTView_320x240` | 7632 | yes | yes | yes |
| `TFTView_240x240` | 48 | — | — | — |
| `TFTView_480x222` | 44 | — | — | — |
| `TFTView_160x80` | 43 | — | — | — |

The three small ones are stubs whose `init()` only calls
`MeshtasticView::init()` (plus `ui_init()` for 480x222). They are therefore not
affected — not because they handle the case correctly, but because they do not
implement this screen at all. `tlora-pager-tft` (`VIEW_480x222`) consequently
shows no pairing PIN either way.

Worth keeping in mind if these views are fleshed out later: copying the pattern
from `TFTView_320x240` would carry the bug along.

---

## Root cause

`TFTView_320x240::init()` — the boot screen hides the label when the logo is
large:

```c
// if boot logo is too big remove the label and center the image
lv_obj_update_layout(objects.boot_logo);
if (lv_obj_get_height(objects.boot_logo) > lv_display_get_vertical_resolution(displaydriver->getDisplay()) / 2) {
    lv_obj_set_pos(objects.boot_logo, 0, 0);
    lv_obj_add_flag(objects.firmware_label, LV_OBJ_FLAG_HIDDEN);
} else {
    lv_label_set_text(objects.firmware_label, firmware_version);
}
```

`enterProgrammingMode()` — the PIN goes into the same label:

```c
state = MeshtasticView::eProgrammingMode;
lv_label_set_text(objects.meshtastic_url, _(">> Programming mode <<"));
lv_label_set_text_fmt(objects.firmware_label, "%06d", db.config.bluetooth.fixed_pin);
lv_obj_set_style_text_font(objects.firmware_label, &ui_font_montserrat_20, LV_PART_MAIN | LV_STATE_DEFAULT);
lv_obj_add_flag(objects.boot_logo, LV_OBJ_FLAG_HIDDEN);
lv_obj_add_flag(objects.boot_logo_button, LV_OBJ_FLAG_HIDDEN);
lv_obj_remove_flag(objects.bluetooth_button, LV_OBJ_FLAG_HIDDEN);
```

`LV_OBJ_FLAG_HIDDEN` is set once at boot and never removed. Note that this
branch already hides `boot_logo`, so the space the label was competing for is
free again — the label should be visible here in any case.

---

## Impact

The PIN is not merely cosmetic: without it the device cannot be paired over
Bluetooth. Anyone shipping branded firmware with a full-bleed boot logo — which
the `branding/` mechanism actively invites — produces devices that look fine
until a user tries to pair one.

The failure is also easy to misattribute. It only appears once the custom logo
*works*; while `loadBootImage()` still fails and the built-in logo is used, the
image stays under the threshold and the PIN shows normally.

---

## Proposed fix

One line, in the programming-mode branch:

```diff
         state = MeshtasticView::eProgrammingMode;
         lv_label_set_text(objects.meshtastic_url, _(">> Programming mode <<"));
         lv_label_set_text_fmt(objects.firmware_label, "%06d", db.config.bluetooth.fixed_pin);
+        lv_obj_remove_flag(objects.firmware_label, LV_OBJ_FLAG_HIDDEN);
         lv_obj_set_style_text_font(objects.firmware_label, &ui_font_montserrat_20, LV_PART_MAIN | LV_STATE_DEFAULT);
         lv_obj_add_flag(objects.boot_logo, LV_OBJ_FLAG_HIDDEN);
```

We have been running this patch against
`1c45ebc7433acb8ba3fe96a6f7deca9c43fa54cf` and it restores the PIN while
keeping the full-screen boot logo.

### Alternative

Clearing the flag inside `updateBootMessage()` as well would fix a related
case: boot messages written through it are invisible under the same condition.
Whether that is desirable is a design call — suppressing them behind a large
logo may well be intended, whereas suppressing the pairing PIN is not.

---

## Secondary observation

`FileLoader::loadBootImage()` reports success purely from the source pointer:

```c
bool FileLoader::loadBootImage(lv_obj_t *img)
{
    lv_image_set_src(img, FL_DRIVE_LETTER "/boot/logo.png");
    return lv_image_get_src((lv_obj_t *)img) != nullptr;
}
```

This happens to work because LVGL 9 clears the source when it cannot read the
image header, but it makes the fallback in the caller depend on an
implementation detail of `lv_image_set_src()` rather than on an explicit check.
Not a bug we hit — noting it in case it is of interest.
