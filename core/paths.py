# This file is part of LazyFarmers.
# Copyright (c) 2025-Present Routo
#
# LazyFarmers is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# You should have received a copy of the GNU General Public License
# along with LazyFarmers. If not, see <https://www.gnu.org/licenses/>.


"""
Author: Routo
LazyFarmers - https://github.com/routo-loop/neura-self
"""


import os
import shutil

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUNDLED_CONFIG_DIR = os.path.join(BASE_DIR, 'config')

# hosts with ephemeral disks (Railway, docker) point this at a mounted volume so
# tokens, dashboard credentials and stats survive a redeploy. Railway sets
# RAILWAY_VOLUME_MOUNT_PATH by itself as soon as a volume is attached.
DATA_ROOT = os.environ.get('LAZYFARMERS_DATA_ROOT') or os.environ.get('RAILWAY_VOLUME_MOUNT_PATH') or BASE_DIR
CONFIG_DIR = os.path.join(DATA_ROOT, 'config')
DATA_DIR = os.path.join(DATA_ROOT, 'data')

os.makedirs(CONFIG_DIR, exist_ok=True)
os.makedirs(DATA_DIR, exist_ok=True)

if DATA_ROOT == BASE_DIR and any(key.startswith('RAILWAY_') for key in os.environ):
    print("[!] No volume attached - accounts, tokens and stats will be wiped on the next deploy. "
          "Add a volume in Railway (any mount path) or set LAZYFARMERS_DATA_ROOT.", flush=True)

if CONFIG_DIR != BUNDLED_CONFIG_DIR and os.path.isdir(BUNDLED_CONFIG_DIR):
    for name in os.listdir(BUNDLED_CONFIG_DIR):
        src = os.path.join(BUNDLED_CONFIG_DIR, name)
        dst = os.path.join(CONFIG_DIR, name)
        if os.path.isfile(src) and not os.path.exists(dst):
            shutil.copy2(src, dst)
