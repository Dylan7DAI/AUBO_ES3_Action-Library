from __future__ import annotations

import time
from typing import Any, Dict, List, Mapping, Optional

from .config import GripperConfig
from .sdk_client import AuboSdkClient, AuboSdkError


CONFIRM = "I_UNDERSTAND_THIS_WILL_MOVE_THE_ROBOT"

REG_POSITION = 0x9C40
REG_FORCE = 0x9C41
REG_CURRENT_POSITION = 0x9C45
REG_TORQUE = 0x9C46
REG_DONE = 0x9C47
REG_FIND_STROKE = 0x9C48
REG_STROKE_NOT_FOUND = 0x9C49
REG_SPEED = 0x9C4A
REG_SPEED_SAVE = 0x9C4B
REG_AUTO_FIND_STROKE = 0x9C9A
REG_SET_ADDRESS = 0x9C9B

MODBUS_FC_WRITE_SINGLE = 0x06
MODBUS_FC_WRITE_MULTIPLE = 0x10
MODBUS_SIGNAL_HOLDING_REGISTER = 3
SIGNAL_PREFIX = "codex_lebo_"


def clamp_percent(value: int) -> int:
    value = int(value)
    if value < 0:
        return 0
    if value > 100:
        return 100
    return value


def crc16_modbus(payload: List[int]) -> int:
    crc = 0xFFFF
    for byte in payload:
        crc ^= byte & 0xFF
        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return crc & 0xFFFF


def write_single_register_frame(slave_id: int, register: int, value: int) -> List[int]:
    payload = [
        slave_id & 0xFF,
        0x06,
        (register >> 8) & 0xFF,
        register & 0xFF,
        (value >> 8) & 0xFF,
        value & 0xFF,
    ]
    crc = crc16_modbus(payload)
    return payload + [crc & 0xFF, (crc >> 8) & 0xFF]


def write_multiple_registers_frame(slave_id: int, register: int, values: List[int]) -> List[int]:
    quantity = len(values)
    payload = [
        slave_id & 0xFF,
        0x10,
        (register >> 8) & 0xFF,
        register & 0xFF,
        (quantity >> 8) & 0xFF,
        quantity & 0xFF,
        quantity * 2,
    ]
    for value in values:
        payload.extend([(value >> 8) & 0xFF, value & 0xFF])
    crc = crc16_modbus(payload)
    return payload + [crc & 0xFF, (crc >> 8) & 0xFF]


def read_holding_register_frame(slave_id: int, register: int, quantity: int = 1) -> List[int]:
    payload = [
        slave_id & 0xFF,
        0x03,
        (register >> 8) & 0xFF,
        register & 0xFF,
        (quantity >> 8) & 0xFF,
        quantity & 0xFF,
    ]
    crc = crc16_modbus(payload)
    return payload + [crc & 0xFF, (crc >> 8) & 0xFF]


def hex_bytes(values: List[int]) -> str:
    return " ".join(f"{value & 0xFF:02X}" for value in values)


def feedback_value(feedback: Dict[str, Any], key: str) -> Optional[int]:
    item = feedback.get("feedback", {}).get(key)
    if not isinstance(item, dict):
        return None
    value = item.get("value")
    return int(value) if value is not None else None


def feedback_errors_ok(feedback: Dict[str, Any]) -> bool:
    values = feedback.get("feedback", {}).values()
    errors = [item.get("error") for item in values if isinstance(item, dict)]
    return bool(errors) and all(error == 0 for error in errors)


class LebaiGripper:
    def __init__(self, client: AuboSdkClient, config: Optional[GripperConfig] = None):
        self.client = client
        self.config = config or client.config.gripper

    def _register_control(self):
        if self.client.rpc is None:
            raise AuboSdkError("尚未连接 AUBO RPC，无法访问 Modbus")
        return self.client.rpc.getRegisterControl()

    def _send_fc06(self, register: int, value: int) -> int:
        reg = self._register_control()
        data = [
            (register >> 8) & 0xFF,
            register & 0xFF,
            (value >> 8) & 0xFF,
            value & 0xFF,
        ]
        return int(reg.modbusSendCustomCommand(
            self.config.modbus_device,
            self.config.slave_id,
            0x06,
            data,
        ))

    def _send_fc10(self, register: int, values: List[int]) -> int:
        reg = self._register_control()
        quantity = len(values)
        data = [
            (register >> 8) & 0xFF,
            register & 0xFF,
            (quantity >> 8) & 0xFF,
            quantity & 0xFF,
            quantity * 2,
        ]
        for value in values:
            data.extend([(value >> 8) & 0xFF, value & 0xFF])
        return int(reg.modbusSendCustomCommand(
            self.config.modbus_device,
            self.config.slave_id,
            0x10,
            data,
        ))

    def _add_read_signal(self, name: str, register: int) -> Any:
        reg = self._register_control()
        if name in list(reg.modbusGetSignalNames()):
            return "exists"
        return int(reg.modbusAddSignal(
            self.config.modbus_device,
            self.config.slave_id,
            int(register),
            MODBUS_SIGNAL_HOLDING_REGISTER,
            name,
            False,
        ))

    def command_preview(self, register: int, value: int, *, function_code: int = MODBUS_FC_WRITE_SINGLE) -> Dict[str, Any]:
        if function_code == MODBUS_FC_WRITE_SINGLE:
            frame = write_single_register_frame(self.config.slave_id, register, value)
        elif function_code == MODBUS_FC_WRITE_MULTIPLE:
            frame = write_multiple_registers_frame(self.config.slave_id, register, [value])
        else:
            raise ValueError(f"不支持的功能码: {function_code}")
        return {
            "modbus_device": self.config.modbus_device,
            "slave_id": self.config.slave_id,
            "function_code": function_code,
            "register_hex": f"0x{register:04X}",
            "register_dec": register,
            "value": value,
            "rtu_frame_hex": hex_bytes(frame),
        }

    def write_register(
        self,
        register: int,
        value: int,
        *,
        execute: bool = False,
        function_code: int = MODBUS_FC_WRITE_SINGLE,
    ) -> Dict[str, Any]:
        preview = self.command_preview(register, value, function_code=function_code)
        preview["executed"] = bool(execute)
        if not execute:
            return preview
        if function_code == MODBUS_FC_WRITE_SINGLE:
            preview["sdk_return"] = self._send_fc06(register, value)
        elif function_code == MODBUS_FC_WRITE_MULTIPLE:
            preview["sdk_return"] = self._send_fc10(register, [value])
        else:
            raise AuboSdkError(f"不支持的 Modbus 功能码: {function_code}")
        return preview

    def set_width(self, percent: int, *, execute: bool = False, function_code: int = MODBUS_FC_WRITE_SINGLE) -> Dict[str, Any]:
        return self.write_register(
            self.config.position_register,
            clamp_percent(percent),
            execute=execute,
            function_code=function_code,
        )

    def set_position(self, percent: int, *, execute: bool = False, function_code: int = MODBUS_FC_WRITE_SINGLE) -> Dict[str, Any]:
        return self.set_width(percent, execute=execute, function_code=function_code)

    def set_force(self, percent: int, *, execute: bool = False, function_code: int = MODBUS_FC_WRITE_SINGLE) -> Dict[str, Any]:
        return self.write_register(
            self.config.force_register,
            clamp_percent(percent),
            execute=execute,
            function_code=function_code,
        )

    def set_speed(
        self,
        percent: int,
        *,
        execute: bool = False,
        persist: bool = False,
        function_code: int = MODBUS_FC_WRITE_SINGLE,
    ) -> Dict[str, Any]:
        register = self.config.speed_save_register if persist else self.config.speed_register
        result = self.write_register(
            register,
            clamp_percent(percent),
            execute=execute,
            function_code=function_code,
        )
        result["persist"] = bool(persist)
        return result

    def disable_auto_find_stroke(
        self,
        *,
        execute: bool = False,
        persist: bool = False,
        function_code: int = MODBUS_FC_WRITE_SINGLE,
    ) -> Dict[str, Any]:
        return self.write_register(
            self.config.auto_find_stroke_register,
            2 if persist else 1,
            execute=execute,
            function_code=function_code,
        )

    def find_stroke(self, *, execute: bool = False, function_code: int = MODBUS_FC_WRITE_SINGLE) -> Dict[str, Any]:
        return self.write_register(
            self.config.find_stroke_register,
            1,
            execute=execute,
            function_code=function_code,
        )

    def feedback(self, *, wait_s: float = 1.0) -> Dict[str, Any]:
        signals: Mapping[str, int] = {
            "position": self.config.current_position_register,
            "torque": self.config.torque_register,
            "done": self.config.done_register,
            "stroke_not_found": self.config.stroke_not_found_register,
            "speed": self.config.speed_register,
        }
        added: Dict[str, int] = {}
        for key, register in signals.items():
            name = f"{SIGNAL_PREFIX}{key}"
            try:
                added[name] = self._add_read_signal(name, register)
            except Exception as exc:
                added[name] = f"add_failed: {exc}"  # type: ignore[assignment]

        reg = self._register_control()
        deadline = time.time() + max(0.0, wait_s)
        names: List[str] = []
        values: List[int] = []
        errors: List[int] = []
        while True:
            names = list(reg.modbusGetSignalNames())
            values = list(reg.modbusGetSignalValues())
            errors = list(reg.modbusGetSignalErrors())
            own_errors = [
                errors[idx]
                for idx, name in enumerate(names)
                if name.startswith(SIGNAL_PREFIX) and idx < len(errors)
            ]
            if own_errors and all(error == 0 for error in own_errors):
                break
            if time.time() >= deadline:
                break
            time.sleep(0.1)
        by_name: Dict[str, Any] = {}
        for idx, name in enumerate(names):
            if not name.startswith(SIGNAL_PREFIX):
                continue
            key = name[len(SIGNAL_PREFIX):]
            by_name[key] = {
                "name": name,
                "value": values[idx] if idx < len(values) else None,
                "error": errors[idx] if idx < len(errors) else None,
            }
        return {
            "modbus_device": self.config.modbus_device,
            "slave_id": self.config.slave_id,
            "added_signals": added,
            "feedback": by_name,
            "is_done": bool(by_name.get("done", {}).get("value") == 1),
        }

    def move(
        self,
        width: int,
        *,
        force: Optional[int] = None,
        speed: Optional[int] = None,
        execute: bool = False,
        wait: bool = False,
        timeout_s: float = 5.0,
        command_delay_s: float = 0.3,
        position_tolerance: int = 3,
        verify: bool = True,
        verify_timeout_s: float = 4.0,
        function_code: int = MODBUS_FC_WRITE_SINGLE,
    ) -> Dict[str, Any]:
        results: List[Dict[str, Any]] = []
        command_delay_s = max(0.0, float(command_delay_s))
        target_width = clamp_percent(width)
        if force is not None:
            results.append(self.set_force(force, execute=execute, function_code=function_code))
            if execute and command_delay_s:
                time.sleep(command_delay_s)
        if speed is not None:
            results.append(self.set_speed(speed, execute=execute, function_code=function_code))
            if execute and command_delay_s:
                time.sleep(command_delay_s)
        results.append(self.set_width(target_width, execute=execute, function_code=function_code))

        feedback: Optional[Dict[str, Any]] = None
        reached = None
        if execute and (wait or verify):
            deadline = time.time() + (timeout_s if wait else verify_timeout_s)
            while time.time() < deadline:
                feedback = self.feedback(wait_s=0.3)
                current_position = feedback_value(feedback, "position")
                reached = (
                    current_position is not None
                    and abs(current_position - target_width) <= int(position_tolerance)
                )
                if reached or (wait and feedback.get("is_done")):
                    break
                if not wait and feedback_errors_ok(feedback) and time.time() >= deadline:
                    break
                time.sleep(0.1)
            else:
                if wait:
                    raise AuboSdkError("夹爪动作执行超时未完成")

        return {
            "executed": bool(execute),
            "width": target_width,
            "force": None if force is None else clamp_percent(force),
            "speed": None if speed is None else clamp_percent(speed),
            "function_code": function_code,
            "command_delay_s": command_delay_s,
            "position_tolerance": int(position_tolerance),
            "verify_timeout_s": float(verify_timeout_s),
            "results": results,
            "feedback": feedback,
            "position_reached": reached,
        }

    def status(self) -> Dict[str, Any]:
        reg = self._register_control()
        result: Dict[str, Any] = {
            "modbus_device": self.config.modbus_device,
            "known_registers": {
                "width": f"0x{self.config.position_register:04X}",
                "force": f"0x{self.config.force_register:04X}",
                "current_position": f"0x{self.config.current_position_register:04X}",
                "torque": f"0x{self.config.torque_register:04X}",
                "done": f"0x{self.config.done_register:04X}",
                "find_stroke": f"0x{self.config.find_stroke_register:04X}",
                "stroke_not_found": f"0x{self.config.stroke_not_found_register:04X}",
                "speed": f"0x{self.config.speed_register:04X}",
                "speed_save": f"0x{self.config.speed_save_register:04X}",
                "auto_find_stroke": f"0x{self.config.auto_find_stroke_register:04X}",
                "set_address": f"0x{self.config.set_address_register:04X}",
            },
            "read_current_position_request_hex": hex_bytes(
                read_holding_register_frame(
                    self.config.slave_id,
                    self.config.current_position_register,
                    1,
                )
            ),
        }
        try:
            result["signal_names"] = list(reg.modbusGetSignalNames())
            result["signal_types"] = list(reg.modbusGetSignalTypes())
            result["signal_values"] = list(reg.modbusGetSignalValues())
            result["signal_errors"] = list(reg.modbusGetSignalErrors())
        except Exception as exc:
            result["signal_read_error"] = str(exc)
        try:
            result["device_status"] = int(reg.getModbusDeviceStatus(self.config.modbus_device))
        except Exception as exc:
            result["device_status_error"] = str(exc)
        return result
