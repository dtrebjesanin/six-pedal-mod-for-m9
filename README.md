# Six-pedal mod for the Line 6 M9 (unofficial)

> [!IMPORTANT]
> This is an unofficial community project. It is not supported, endorsed or maintained by Line 6. "Line 6" and "M9" are trademarks of their owners, and I use them here only to say which pedal and which official firmware the mod works with.

> [!WARNING]
> **Use it at your own risk.** Every flash erases all saved scenes and the global settings of the pedal. If a scene matters to you, write down its settings before you flash. To go back, flash the official 2.04 file from the Line 6 site ([back to the official 2.04](#back-to-the-official-204)).

I made this mod for my own M9. The stock firmware runs three effects at a time (one pedal per column, A or B), and with the mod all six pedals are independent effects at the same time. This repository holds no Line 6 firmware: the browser page `index.html` takes the official 2.04 file that you download yourself, makes the mod in your browser and sends it to the pedal over MIDI. The Python scripts `patch.py` and `flash.py` do the same job from the command line, in case the page doesn't work for you.

## What the mod does

All six pedals run at the same time, in the signal order 1A 1B 2A 2B 3A 3B. Each pedal has its own switch and LED, and tap tempo works for both pedals of a column.

With six effects running, the DSP can run out of time, so the mod also adds an overload warning to the display:

- Above 90 % DSP load, a small box with the percent shows up next to the model name.
- When an audio block is lost, the display shows `DSP OVERLOAD` full screen for 2 seconds, and then an `OVL` box for as long as the overload lasts.

Every time you switch the pedal on, it shows `UNOFFICIAL FIRMWARE` / `SIX-PEDAL MOD 1.0` for 3 seconds, so it's always clear which firmware is on it. The MIDI page of the global settings also shows `v2.M1` instead of `v2.04`.

### Limits

- An effect that is off still uses its full DSP time. Switching a pedal off won't end an overload, only a lighter model or another scene will.
- Six heavy models together can overload the DSP.
- In my own use, most common pedal combinations don't get near an overload, or even near 90 % (where the percent box shows up).
- In my tests an overload did no harm to the pedal. While testing I ran the DSP over the limit several times (up to about 103 %): it then skips audio blocks, so the sound breaks up (I turned the volume down for the load test), but the pedal stays in control and keeps its settings.
- I tested it on one pedal (mine) and only with Windows 11: the browser page in Chrome, and the Python scripts from the command line. macOS and Linux are not tested. The commands below use `uv run --python 3.12`, so uv gets a Python for which python-rtmidi has ready-made packages, and that might be enough on macOS and Linux too, but I haven't tried it.

## In the browser

The easiest way is the page `index.html` from this repository. It runs in Chrome or Edge on a desktop PC, needs no install and does the same job as the two Python scripts: it checks your 2.04 file, makes the mod in the browser and sends it to the pedal with the same pauses as `flash.py`, and at the end it reads the version. It can also flash the official 2.04 back. Your file stays on your PC (the page is built so that it can't connect to anything on the internet, so it can't upload your file either).

Open it at [dtrebjesanin.github.io/six-pedal-mod-for-m9](https://dtrebjesanin.github.io/six-pedal-mod-for-m9/), or download this repository and open `index.html` from the folder. You need the same things as for the scripts (see [what you need](#what-you-need)), except Python and uv.

The page takes you through six steps:

1. Choose "Install the six-pedal mod 1.0" (or "Back to the official 2.04").
2. Drop the official 2.04 file on the page. It checks that the file really is the official 2.04, the same check that `patch.py` does.
3. Click "Allow MIDI" and choose the MIDI out and in of your interface. The browser asks you to allow MIDI access, and the page needs it to send the update. Close other programs that use the interface first (I had to close REAPER).
4. Put the pedal into FLASH UPDATE MODE ([step 2](#2-put-the-pedal-into-flash-update-mode) below) and click "Check the pedal". The page only goes on when the pedal answers from that mode.
5. Leave the pauses on "Normal", tick the own-risk box and click "Flash". After a 10 second countdown the flash takes about 6 minutes. Keep the tab open, the PC awake and the pedal plugged in until it has restarted.
6. After the restart, click "Read version": `02M1` is the six-pedal mod 1.0 and `0204` is the official firmware.

If the page doesn't work in your browser, the Python scripts below do the same thing.

## What you need

- An M9 and the official 2.04 firmware file, `M7_BFlash_2_04_00.m9f`, which you download directly from the Line 6 site ([line6.com/software](https://line6.com/software/), choose M9 and Firmware).
- A MIDI interface, connected both ways: the MIDI out of the PC to the MIDI in of the M9, and the MIDI out of the M9 to the MIDI in of the PC.
- Chrome or Edge on a desktop PC for the browser page, or Python and uv for the scripts (the next two points).
- Python 3.8 or later for `patch.py`, which uses only the standard library. I tested it with 3.8, 3.12 and 3.14. You can run it with your own Python directly, or through uv as in step 1.
- [uv](https://docs.astral.sh/uv/) for `flash.py`, which needs the python-rtmidi package. The `uv run` commands below fetch it for you.
- The files of this repository, with `six-pedal-mod-1.0.patch.json` next to `patch.py`.

## Steps with Python

### 1. Make the mod file

Download the official 2.04 file from the Line 6 site, keep it unchanged, and run (with the full path of the file if it's in another folder):

```
uv run --python 3.12 python patch.py M7_BFlash_2_04_00.m9f
```

`patch.py` checks that the file really is the official 2.04 and refuses anything else (the 2.02 file, for example, or a file that is already modded). It writes `six-pedal-mod-1.0.m9f` next to the input file and then says that the content matches the tested image, with the sha256 of that content. The sha256 of the file itself can differ from PC to PC, because it depends on the zlib of your Python, but the content that goes to the pedal is the same. With `-o` you can choose another output path.

In the flash commands below I write the bare file names. Use the path that `patch.py` printed after "Written:" instead, in quotes if it has spaces.

### 2. Put the pedal into FLASH UPDATE MODE

`flash.py` needs the pedal in this mode:

1. Switch the pedal off.
2. Hold footswitches 1A and 2A and switch the pedal on (plug in the power).
3. Keep holding them until the display reads `FLASH UPDATE MODE` / `WAITING COMMAND`.

Before you flash, close every other program that uses the MIDI interface. On my PC, Windows gave a MIDI port to one program at a time (I had to close REAPER first), so a program that holds the port can keep `flash.py` from opening it.

### 3. Flash the mod file

The official Line 6 updater (Line 6 Monkey) rejects custom files, so `flash.py` sends the file over MIDI instead. First, list the MIDI ports:

```
uv run --python 3.12 --with python-rtmidi python flash.py --list
```

For `--port`, give a part of the port name that is in both lists, without the number at the end (for example `--port focusrite`), in quotes if it has a space. On Windows the number at the end can differ between the in and the out list for the same interface. Without `--yes`, `flash.py` sends nothing, which makes a dry run a check of the file and the port name:

```
uv run --python 3.12 --with python-rtmidi python flash.py six-pedal-mod-1.0.m9f --port <name>
```

Then flash it for real:

```
uv run --python 3.12 --with python-rtmidi python flash.py six-pedal-mod-1.0.m9f --port <name> --yes
```

`flash.py` prints the notice and the warning again and counts down 10 seconds before it sends the first message. A flash takes about 6 minutes, with a 5 second pause after each erase, section start and section end. That was enough for all my flashes since 13 October 2026, but another pedal might need more time (see [if a flash fails](#if-a-flash-fails)).

The pedal answers each message, and `flash.py` counts the answers and shows only the ones that are not OK (add `--verbose` to see all of them). At the end it says whether the pedal confirmed everything.

Don't switch the pedal off or unplug it until it has restarted.

In FLASH UPDATE MODE a black overlay covers the display, and after the restart a bit of that black "residue" can stay on the screen. Adjusting the contrast in the setup options is enough to bring it back to normal (at least on my unit).

### 4. Check the result

After the restart the pedal shows `UNOFFICIAL FIRMWARE` / `SIX-PEDAL MOD 1.0` for 3 seconds. You can also ask it for its version:

```
uv run --python 3.12 --with python-rtmidi python flash.py --port <name> --identity
```

`02M1` is the six-pedal mod 1.0 and `0204` is the official firmware.

## Back to the official 2.04

Every flash erases the scenes and the global settings, so write down the settings of any scene you made on the mod first.

In the browser, choose "Back to the official 2.04" in step 1 of the page and drop the same official 2.04 file. The page sends it to the pedal unchanged, and the rest works the same as for the mod.

With Python, put the pedal into FLASH UPDATE MODE ([step 2](#2-put-the-pedal-into-flash-update-mode)) and flash the official file with `flash.py`, the same way as the mod:

```
uv run --python 3.12 --with python-rtmidi python flash.py M7_BFlash_2_04_00.m9f --port <name> --yes
```

After it, the start-up message is gone and the version is `0204` ("Read version" on the page, or `--identity` with `flash.py`).

### If a flash fails

Switch the pedal off, enter FLASH UPDATE MODE again and flash again. If it fails again, give the pedal more time with longer pauses (a flash then takes 22 to 23 minutes). On the page that's the "Slow" choice in step 5, and with `flash.py` it's:

```
uv run --python 3.12 --with python-rtmidi python flash.py six-pedal-mod-1.0.m9f --port <name> --yes --erase-s 120 --start-s 120 --commit-s 30
```

On my pedal one early flash ended with "Download Fail 0x02: Format Error", and the next one worked. That's all I can say from one pedal, and I can't promise that a repeated flash fixes every failure.

## Licence

The mod is free, under the PolyForm Noncommercial License 1.0.0 (see [LICENSE](LICENSE)). You can use it, change it and share it, but not sell it or use it commercially, and every copy keeps the `Required Notice` lines from the LICENSE file. I made it for my own pedal and I'm sharing it as it is, with no promise of support or updates. I hope it's useful to a few other M9 owners.
