# Third-party notices

`fonts/` holds **Source Sans Pro** 2.045 (Regular and Semibold), Copyright
2010-2018 Adobe, with Reserved Font Name 'Source', from
https://github.com/adobe-fonts/source-sans, under the SIL Open Font License
1.1: see `fonts/LICENSE.txt`.

The Windows package (AH-Quick-Shots-Windows.zip) also holds, unchanged:

- **Python** 3.15.2, the embeddable package for Windows from
  https://www.python.org/downloads/, under the Python Software Foundation
  License: see `python/LICENSE.txt`. Only its `python315._pth` gets one line,
  the app's folder.
- **FFmpeg** 9.0.2, `ffmpeg.exe` of the essentials build by gyan.dev
  (https://www.gyan.dev/ffmpeg/builds/), under the GNU General Public License
  version 3: see `ffmpeg/LICENSE`. Its source code is at
  https://ffmpeg.org/releases/ffmpeg-9.0.2.tar.xz and
  https://github.com/FFmpeg/FFmpeg/commit/946fcce07b; `ffmpeg/README.txt`
  lists the libraries built into it. AH Quick Shots runs it as a separate
  program and does not link to it.

The ready build of the page in `ui/dist/` contains code from:

- **React**, **React DOM** and **Scheduler**
  Copyright (c) Meta Platforms, Inc. and affiliates. — https://github.com/facebook/react
- **Phosphor Icons** (`@phosphor-icons/react`)
  Copyright (c) 2020 Phosphor Icons — https://github.com/phosphor-icons/react

It is built with (not included in the build):

- **Vite**
  Copyright (c) 2019-present, VoidZero Inc. and Vite contributors — https://github.com/vitejs/vite
- **@vitejs/plugin-react**
  Copyright (c) 2019-present, Yuxi (Evan) You and Vite contributors — https://github.com/vitejs/vite-plugin-react

All of them are released under the MIT License:

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
