# Framefetch

Framefetch is a terminal hardware dashboard designed around the physical layout of the **Framework Laptop 13**.

Instead of showing hardware information as a normal list, Framefetch renders the machine as an ASCII-style motherboard and places information such as CPU, GPU, RAM, storage, battery, networking, Bluetooth devices, displays, and Framework expansion ports around it.

It can be used as a static Fastfetch-like system summary, or launched with `--watch` to behave more like a lightweight terminal system monitor.

## About this project

I am just a student working on this project in my free time.

Framefetch is not a professionally engineered hardware-monitoring application, and parts of the project have been developed with the help of AI and vibe coding. (i'm very dyslexic in fact, this ENTIRE readme is AI... minus this comment)

The goal is mainly to create something ""useful"", customizable, and visually Framework centric.

Because of that, bugs, imperfect implementations, strange hardware edge cases, and less-than-perfect code are absolutely possible.

Contributions and improvements are very welcome.

## Current target

The current version is primarily designed for the:

**Framework Laptop 13 — AMD Ryzen AI 300 Series**

The hardware detection is Linux-oriented and has ONLY been developed and tested around my own Framework 13 setup.

Different distributions, kernels, firmware versions, Framework generations, peripherals, and hardware configurations may expose information differently.

## Main features

Framefetch can currently display or detect information including:

* CPU model
* CPU usage
* CPU frequency
* CPU temperature
* GPU model
* GPU usage
* GPU frequency
* RAM usage
* individual SODIMM information
* internal NVMe storage usage
* mounted external storage
* laptop battery information
* peripheral battery levels when exposed by the system
* Wi-Fi information
* network RX/TX activity
* connected Bluetooth devices
* internal and external display information
* fan RPM
* Framework expansion-port information
* USB / Type-C port calibration
* live refresh through `--watch`

Some hardware information depends entirely on what Linux, the kernel, firmware, UPower, BlueZ, KScreen, sysfs, and other system interfaces expose.

## Files

The project is intentionally split into two main files:

```text
Framefetch_FW13_1.py
config.py
```

`Framefetch_FW13_1.py` contains the actual hardware detection, rendering, live-monitoring, calibration, and program logic.

`config.py` contains the values that users are most likely to want to customize.

Keep both files in the **same directory**.

## Configuration

Most visual customization should be done inside:

```text
config.py
```

You can change things such as:

* CPU thresholds
* GPU thresholds
* RAM thresholds
* SSD/storage thresholds
* battery thresholds
* CPU temperature thresholds
* fan thresholds
* colors
* bar widths
* layout spacing
* Framework port overrides
* logo color
* logo behavior in `--watch`

The intention is that most users should not need to modify the main Framefetch code just to change how the dashboard looks.

## Logos

Framefetch currently includes four selectable logos:

* Framework
* Arch Linux
* CachyOS
* Ubuntu

The active logo is selected in `config.py` by changing the final logo variable.

For example:

```python
LOGO = FW_LOGO
```

can become:

```python
LOGO = ARCH_LOGO
```

or:

```python
LOGO = CACHYOS_LOGO
```

or:

```python
LOGO = UBUNTU_LOGO
```

No other part of the program should need to be modified.

Additional logos are welcome.

As long as a new logo fits the expected ASCII layout, it should be possible to define another logo variable and assign it to `LOGO`.

## Logo color in watch mode

The logo can remain static or change color while Framefetch is running in `--watch` mode.

This behavior is configured through `config.py`.

For example, the logo can use a single fixed color or rotate through a list of colors.

The available cycle can also be customized.

## Colors and thresholds

CPU, GPU, RAM, SSD/storage, battery, temperature, and fan colors can be configured independently.

For example, CPU usage can have its own warning and critical percentages:

```python
CPU_WARNING_PERCENT = 50
CPU_CRITICAL_PERCENT = 80
```

and its own colors:

```python
COLOR_CPU_OK = GREEN
COLOR_CPU_WARNING = YELLOW
COLOR_CPU_CRITICAL = RED
```

The same idea is used for the other hardware categories.

You can use the colors already defined in the configuration file, or add another ANSI color yourself.

In principle, adding another valid ANSI color and assigning it to one of the configurable color variables should not break Framefetch.

For example:

```python
MY_COLOR = "\033[38;5;123m"

COLOR_CPU_OK = MY_COLOR
```

The configuration is intentionally meant to be easy to experiment with.

## Framework port calibration

Framework ports can expose devices through several different Linux subsystems, and the physical connector number is not always obvious from software alone.

Framefetch therefore includes calibration commands for USB and Type-C mappings.

The program stores learned mappings in the user's configuration directory so that devices can later be associated with the correct physical Framework expansion slot.

Useful commands include:

```bash
python Framefetch_FW13_1.py --calibrate-typec N
```

```bash
python Framefetch_FW13_1.py --calibrate-usb N
```

```bash
python Framefetch_FW13_1.py --show-port-map
```

where `N` is the physical Framework port number.

Diagnostics are also available through:

```bash
python Framefetch_FW13_1.py --diagnostics
```

## Hardware cache

Some RAM information may require privileged SMBIOS access.

Framefetch can cache that mostly static information once:

```bash
sudo python Framefetch_FW13_1.py --cache-hardware
```

After that, normal launches can reuse the cached hardware data without repeatedly requiring privileged access.

## Static mode

Run:

```bash
python Framefetch_FW13_1.py
```

to print a single hardware snapshot.

## Watch mode

Run:

```bash
python Framefetch_FW13_1.py --watch
```

to continuously redraw the dashboard.

The default refresh interval is currently 3 seconds.

You can also provide another interval:

```bash
python Framefetch_FW13_1.py --watch 2
```

## Framework 12 and Framework 13 variants

I have also uploaded / created ASCII motherboard drawings for other Framework layouts, including Framework 12 and other Framework 13-related layouts.

However, I currently do **not** plan to personally maintain or implement those versions.

The drawings may still be useful as a starting point for contributors who want to create additional Framefetch variants.

## Contributions

Framefetch is an open-source project.

Anyone is welcome to contribute:

* additional Linux distribution logos
* improved hardware detection
* support for more Framework generations
* Framework 12 versions
* alternative Framework 13 layouts
* additional Framework boards
* better port detection
* new hardware cards
* compatibility fixes
* distro-specific fixes
* performance improvements
* cleanup or refactoring
* documentation improvements
* new features

Pull requests, forks, experiments, fixes, and derivative versions are welcome.

If you own hardware that I do not have, contributions are especially useful because I cannot properly test hardware-specific implementations myself.

## Project philosophy

Framefetch is supposed to remain:

* readable
* customizable
* terminal-friendly
* visually tied to Framework hardware
* relatively easy to modify
* useful without pretending to be a perfect universal hardware-monitoring solution

It started as a personal project for my own Framework laptop.

If other people find it useful, improve it, add support for their hardware, or turn it into something better, that is exactly what open source is for.
