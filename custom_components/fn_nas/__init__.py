import logging
import asyncio
from contextlib import suppress
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .const import (
    DOMAIN, DATA_UPDATE_COORDINATOR, PLATFORMS, CONF_ENABLE_DOCKER
)
from .coordinator import FlynasCoordinator, UPSDataUpdateCoordinator
from .entity_helpers import disk_key

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry):
    config = {**entry.data, **entry.options}
    coordinator = FlynasCoordinator(hass, config, entry)
    # 直接初始化，不阻塞等待NAS上线
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {
        DATA_UPDATE_COORDINATOR: coordinator,
        "ups_coordinator": None,
        CONF_ENABLE_DOCKER: coordinator.config.get(CONF_ENABLE_DOCKER, False)
    }
    entry.async_on_unload(entry.add_update_listener(async_update_entry))
    # 异步后台初始化
    hass.data[DOMAIN][entry.entry_id]["setup_task"] = hass.async_create_task(
        async_delayed_setup(hass, entry, coordinator)
    )
    return True

async def async_delayed_setup(hass: HomeAssistant, entry: ConfigEntry, coordinator: FlynasCoordinator):
    try:
        # 检查配置条目状态，只有在 SETUP_IN_PROGRESS 时才调用 async_config_entry_first_refresh
        from homeassistant.config_entries import ConfigEntryState
        if entry.state == ConfigEntryState.SETUP_IN_PROGRESS:
            await coordinator.async_config_entry_first_refresh()
        else:
            # 如果配置条目已经加载，则直接刷新数据
            await coordinator.async_refresh()
        enable_docker = coordinator.config.get(CONF_ENABLE_DOCKER, False)
        if enable_docker:
            from .docker_manager import DockerManager
            coordinator.docker_manager = DockerManager(coordinator)
            _LOGGER.debug("已启用Docker容器监控")
        else:
            coordinator.docker_manager = None
            _LOGGER.debug("未启用Docker容器监控")
        ups_coordinator = UPSDataUpdateCoordinator(hass, coordinator.config, coordinator)
        if entry.state == ConfigEntryState.SETUP_IN_PROGRESS:
            await ups_coordinator.async_config_entry_first_refresh()
        else:
            await ups_coordinator.async_refresh()
        hass.data[DOMAIN][entry.entry_id]["ups_coordinator"] = ups_coordinator
        await async_migrate_disk_entity_ids(hass, entry, coordinator)
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
        _LOGGER.info("飞牛NAS集成初始化完成")
    except Exception as e:
        _LOGGER.error("飞牛NAS集成初始化失败: %s", str(e))
        await coordinator.async_disconnect()
        if hasattr(coordinator, '_ping_task') and coordinator._ping_task:
            coordinator._ping_task.cancel()

async def async_update_entry(hass: HomeAssistant, entry: ConfigEntry):
    """更新配置项"""
    # 卸载现有集成
    await async_unload_entry(hass, entry)
    # 重新加载集成
    await async_setup_entry(hass, entry)


async def async_migrate_disk_entity_ids(
    hass: HomeAssistant,
    entry: ConfigEntry,
    coordinator: FlynasCoordinator,
) -> None:
    """Rename existing disk registry entries to serial-based entity IDs."""
    entity_registry = er.async_get(hass)
    disks = coordinator.data.get("disks", [])

    for disk in disks:
        stable_disk_key = disk_key(disk)
        unique_id = f"{entry.entry_id}_disk_{stable_disk_key}_health"
        current_entity_id = entity_registry.async_get_entity_id(
            "sensor",
            DOMAIN,
            unique_id,
        )
        if not current_entity_id:
            continue

        target_entity_id = f"sensor.disk_{stable_disk_key}_info"
        if current_entity_id == target_entity_id:
            continue

        if entity_registry.async_get(target_entity_id):
            _LOGGER.warning(
                "跳过硬盘实体重命名，目标实体已存在: %s -> %s",
                current_entity_id,
                target_entity_id,
            )
            continue

        try:
            entity_registry.async_update_entity(
                current_entity_id,
                new_entity_id=target_entity_id,
            )
            _LOGGER.info(
                "已将硬盘实体按序列号重命名: %s -> %s",
                current_entity_id,
                target_entity_id,
            )
        except ValueError as err:
            _LOGGER.warning(
                "硬盘实体重命名失败: %s -> %s: %s",
                current_entity_id,
                target_entity_id,
                err,
            )

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry):
    """卸载集成"""
    # 获取集成数据
    domain_data = hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
    unload_ok = True
    
    if DATA_UPDATE_COORDINATOR in domain_data:
        coordinator = domain_data[DATA_UPDATE_COORDINATOR]
        ups_coordinator = domain_data.get("ups_coordinator")
        setup_task = domain_data.get("setup_task")
        if setup_task and not setup_task.done():
            setup_task.cancel()
            with suppress(asyncio.CancelledError):
                await setup_task
        
        # 卸载平台
        unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
        
        if unload_ok:
            # 关闭主协调器的SSH连接
            await coordinator.async_disconnect()
            
            # 关闭UPS协调器（如果存在）
            if ups_coordinator:
                await ups_coordinator.async_shutdown()
            
            # 取消监控任务（如果存在）
            if hasattr(coordinator, '_ping_task') and coordinator._ping_task and not coordinator._ping_task.done():
                coordinator._ping_task.cancel()
                
            # 从DOMAIN中移除该entry的数据
            hass.data[DOMAIN].pop(entry.entry_id, None)
    
    return unload_ok
