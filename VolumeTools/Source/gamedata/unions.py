"""Native union families needed to read the LevelData prefix and function areas."""
FAMILY_BASES = {
    "ActionBase": "Beyond_Gameplay_Actions_ActionBaseForMemoryPack",
    "PureGetter": "Beyond_Gameplay_Actions_PureGetterForMemoryPack",
    "ActionHeader": "Beyond_Gameplay_Actions_ActionHeaderForMemoryPack",
    # Not a LevelScript family, but BuffData and SkillData readers key their
    # ability actions the same way and go stale the same way.
    "AbilityActionData": "Beyond_Gameplay_Core_AbilityAction_AbilityActionDataForMemoryPack",
    "GameCondition": "Beyond_Gameplay_GameConditionForMemoryPack",
    "BaseComponentData": "Beyond_Gameplay_BaseComponentDataForMemoryPack",
    # The selector unions inside every TargetSettings (Buff/Skill finders,
    # validators and post-processors) renumber the same way.
    "SelectorFinder": "Beyond_Gameplay_Core_Selector_Finder_DataForMemoryPack",
    "SelectorValidator": "Beyond_Gameplay_Core_Selector_Validator_DataForMemoryPack",
    "SelectorPostProcessor": "Beyond_Gameplay_Core_Selector_PostProcessor_DataForMemoryPack",
    "LevelScriptModuleData": "Beyond_Gameplay_LevelScriptModuleDataForMemoryPack",
    # Conditions nested in LevelData function areas and interactive progress
    # locks, the function-area settings union, and SpawnerConfig wave actions.
    "ConditionRuntimeBase": "Beyond_Gameplay_ConditionRuntimeBaseForMemoryPack",
    "FunctionAreaSpecificData": "Beyond_Gameplay_LevelFunctionAreaData_FunctionAreaSpecificDataForMemoryPack",
    "SpawnerActionData": "Beyond_Gameplay_SpawnerActionDataForMemoryPack",
    # The two-member sub-action union inside NPC patrol points; its formatter
    # dispatches through a compare chain rather than a jump table.
    "PatrolSubActionData": "Beyond_Gameplay_Core_PatrolSubActionDataForMemoryPack",
}
_WRAPPER_PREFIXES = ("Beyond.MemoryPack.Beyond_Gameplay_Actions_", "Beyond.MemoryPack.Beyond_Gameplay_")
_WRAPPER_SUFFIX = "ForMemoryPack"


def short_name(wrapper_name: str) -> str:
    """``...Beyond_Gameplay_Actions_IfElseActionForMemoryPack`` -> ``IfElseAction``.

    Members declared directly in ``Beyond.Gameplay`` (``ActiveRopeAnim``) lose
    that shorter prefix instead.
    """
    for prefix in _WRAPPER_PREFIXES:
        if wrapper_name.startswith(prefix):
            wrapper_name = wrapper_name[len(prefix):]
            break
    return wrapper_name.removesuffix(_WRAPPER_SUFFIX)
