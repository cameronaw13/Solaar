## Copyright (C) 2012-2013  Daniel Pavel
## Copyright (C) 2014-2024  Solaar Contributors https://pwr-solaar.github.io/Solaar/
##
## This program is free software; you can redistribute it and/or modify
## it under the terms of the GNU General Public License as published by
## the Free Software Foundation; either version 2 of the License, or
## (at your option) any later version.
##
## This program is distributed in the hope that it will be useful,
## but WITHOUT ANY WARRANTY; without even the implied warranty of
## MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
## GNU General Public License for more details.
##
## You should have received a copy of the GNU General Public License along
## with this program; if not, write to the Free Software Foundation, Inc.,
## 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.

import json
import logging
import os
import threading

import yaml

from logitech_receiver.common import NamedInt

from solaar import __version__

logger = logging.getLogger(__name__)

_XDG_CONFIG_HOME = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser(os.path.join("~", ".config"))
_yaml_file_path = os.path.join(_XDG_CONFIG_HOME, "solaar", "config.yaml")
_json_file_path = os.path.join(_XDG_CONFIG_HOME, "solaar", "config.json")

_KEY_VERSION = "_version"
_KEY_NAME = "_NAME"
_KEY_WPID = "_wpid"
_KEY_SERIAL = "_serial"
_KEY_MODEL_ID = "_modelId"
_KEY_UNIT_ID = "_unitId"
_KEY_ABSENT = "_absent"
_KEY_SENSITIVE = "_sensitive"
_config = []


def _load():
    loaded_config = []
    if os.path.isfile(_yaml_file_path):
        path = _yaml_file_path
        try:
            with open(_yaml_file_path) as config_file:
                loaded_config = yaml.safe_load(config_file)
        except Exception as e:
            logger.error("failed to load from %s: %s", _yaml_file_path, e)
    elif os.path.isfile(_json_file_path):
        path = _json_file_path
        try:
            with open(_json_file_path) as config_file:
                loaded_config = json.load(config_file)
        except Exception as e:
            logger.error("failed to load from %s: %s", _json_file_path, e)
        loaded_config = _convert_json(loaded_config)
    else:
        path = None
    logger.debug("load => %s", loaded_config)
    global _config
    _config = _parse_config(loaded_config, path)


def _parse_config(loaded_config, config_path):
    current_version = __version__
    parsed_config = [current_version]
    try:
        if not loaded_config:
            return parsed_config

        loaded_version = loaded_config[0]
        discard_derived_properties = loaded_version != current_version
        if discard_derived_properties:
            logger.info(
                "config file '%s' was generated by another version of solaar "
                "(config: %s, current: %s). refreshing detected device capabilities",
                config_path,
                loaded_version,
                current_version,
            )

        for device in loaded_config[1:]:
            assert isinstance(device, dict)
            parsed_config.append(_device_entry_from_config_dict(device, discard_derived_properties))
    except Exception as e:
        logger.warning("Exception processing config file '%s', ignoring contents: %s", config_path, e)
    return parsed_config


def _device_entry_from_config_dict(data, discard_derived_properties):
    divert = data.get("divert-keys")
    if divert:
        sliding = data.get("dpi-sliding")
        if sliding:  # convert old-style dpi-sliding setting to divert-keys entry
            divert[int(sliding)] = 3
        data.pop("dpi-sliding", None)
        gestures = data.get("mouse-gestures")
        if gestures:  # convert old-style mouse-gestures setting to divert-keys entry
            divert[int(gestures)] = 2
        data.pop("mouse-gestures", None)
        # remove any string entries (from bad conversions)
        data["divert-keys"] = {k: v for k, v in divert.items() if isinstance(k, int)}
    if data.get("_sensitive", None) is None:  # make scroll wheel settings default to ignore
        data["_sensitive"] = {
            "hires-smooth-resolution": "ignore",
            "hires-smooth-invert": "ignore",
            "hires-scroll-mode": "ignore",
        }
    if discard_derived_properties:
        data.pop("_absent", None)
        data.pop("_battery", None)
    return _DeviceEntry(**data)


save_timer = None
configuration_lock = threading.Lock()
defer_saves = False  # don't allow configuration saves to be deferred


def save(defer=False):
    global save_timer
    if not _config:
        return
    dirname = os.path.dirname(_yaml_file_path)
    if not os.path.isdir(dirname):
        try:
            os.makedirs(dirname)
        except Exception:
            logger.error("failed to create %s", dirname)
            return
    if not defer or not defer_saves:
        do_save()
    else:
        with configuration_lock:
            if not save_timer:
                save_timer = threading.Timer(5.0, do_save)
                save_timer.start()


def do_save():
    global save_timer
    with configuration_lock:
        if save_timer:
            save_timer.cancel()
            save_timer = None
        try:
            with open(_yaml_file_path, "w") as config_file:
                yaml.dump(_config, config_file, default_flow_style=None, width=150)
            logger.info("saved %s to %s", _config, _yaml_file_path)
        except Exception as e:
            logger.error("failed to save to %s: %s", _yaml_file_path, e)


def _convert_json(json_dict):
    config = [json_dict.get(_KEY_VERSION)]
    for key, dev in json_dict.items():
        key = key.split(":")
        if len(key) == 2:
            dev[_KEY_WPID] = dev.get(_KEY_WPID) if dev.get(_KEY_WPID) else key[0]
            dev[_KEY_SERIAL] = dev.get(_KEY_SERIAL) if dev.get(_KEY_SERIAL) else key[1]
            for k, v in dev.items():
                if isinstance(k, str) and not k.startswith("_") and isinstance(v, dict):  # convert string keys to ints
                    v = {int(dk) if isinstance(dk, str) else dk: dv for dk, dv in v.items()}
                dev[k] = v
            for k in ["mouse-gestures", "dpi-sliding"]:
                v = dev.get(k, None)
                if v is True or v is False:
                    dev.pop(k)
            if "_name" in dev:
                dev[_KEY_NAME] = dev["_name"]
                dev.pop("_name")
            config.append(dev)
    return config


class _DeviceEntry(dict):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)

    def __setitem__(self, key, value):
        super().__setitem__(key, value)
        save(defer=True)

    def update(self, name, wpid, serial, modelId, unitId):
        if name and name != self.get(_KEY_NAME):
            super().__setitem__(_KEY_NAME, name)
        if wpid and wpid != self.get(_KEY_WPID):
            super().__setitem__(_KEY_WPID, wpid)
        if serial and serial != self.get(_KEY_SERIAL):
            super().__setitem__(_KEY_SERIAL, serial)
        if modelId and modelId != self.get(_KEY_MODEL_ID):
            super().__setitem__(_KEY_MODEL_ID, modelId)
        if unitId and unitId != self.get(_KEY_UNIT_ID):
            super().__setitem__(_KEY_UNIT_ID, unitId)

    def get_sensitivity(self, name):
        return self.get(_KEY_SENSITIVE, {}).get(name, False)

    def set_sensitivity(self, name, value):
        sensitives = self.get(_KEY_SENSITIVE, {})
        if sensitives.get(name) != value:
            sensitives[name] = value
            self.__setitem__(_KEY_SENSITIVE, sensitives)


def device_representer(dumper, data):
    return dumper.represent_mapping("tag:yaml.org,2002:map", data)


yaml.add_representer(_DeviceEntry, device_representer)


def named_int_representer(dumper, data):
    return dumper.represent_scalar("tag:yaml.org,2002:int", str(int(data)))


yaml.add_representer(NamedInt, named_int_representer)


# A device can be identified by a combination of WPID and serial number (for receiver-connected devices)
# or a combination of modelId and unitId (for direct-connected devices).
# But some devices have empty (all zero) modelIds and unitIds.  Use the device name as a backup for the modelId.
# The worst situation is a receiver-connected device that Solaar has never seen on-line
# that is directly connected.  Here there is no way to realize that the two devices are the same.
# So new entries are not created for unseen off-line receiver-connected devices
def persister(device):
    def match(wpid, serial, modelId, unitId, c):
        return (wpid and wpid == c.get(_KEY_WPID) and serial and serial == c.get(_KEY_SERIAL)) or (
            modelId and modelId == c.get(_KEY_MODEL_ID) and unitId and unitId == c.get(_KEY_UNIT_ID)
        )

    with configuration_lock:
        if not _config:
            _load()
        entry = None
        # some devices report modelId and unitId as zero so use name and serial for them
        modelId = device.modelId if device.modelId != "000000000000" else device._name if device.modelId else None
        unitId = device.unitId if device.modelId != "000000000000" else device._serial if device.unitId else None
        for c in _config:
            if isinstance(c, _DeviceEntry) and match(device.wpid, device._serial, modelId, unitId, c):
                entry = c
                break
        if not entry:
            if not device.online:  # don't create entry for offline devices
                logger.info("not setting up persister for offline device %s", device._name)
                return
            logger.info("setting up persister for device %s", device.name)
            entry = _DeviceEntry()
            _config.append(entry)
        entry.update(device.name, device.wpid, device.serial, modelId, unitId)
        return entry


def attach_to(device):
    pass
