"""Facts each golden example must produce, written by hand from reading the C,
before running the tool. Only the listed keys are checked; others may vary.

Keys: side_effects ("none" | "yes" | "unknown"), writes_globals, reads_globals,
through (list of (pointer, origin, certainty)), state, local, external, loops,
returns, unsupported (count).
"""
NONE = {"side_effects": "none", "writes_globals": [], "through": [], "state": []}

EXPECTED = {
    "01_add_two": {"add": {**NONE, "loops": 0, "returns": 1, "local": [], "external": []}},
    "02_precedence": {"mixed": {**NONE}},
    "03_left_assoc": {"chain": {**NONE}},
    "04_int_division": {"divide": {**NONE}},
    "05_compound_ops": {"compound": {**NONE}},   # x is a copy; changing it affects no caller
    "06_increment": {"steps": {**NONE}},
    "07_if_else": {"sign": {**NONE, "returns": 2}},
    "08_else_if_chain": {"grade": {**NONE, "returns": 4}},
    "09_while": {"count_down": {**NONE, "loops": 1}},
    "10_do_while": {"digits": {**NONE, "loops": 1}},
    "11_repeat_simple": {"sum_to": {**NONE, "loops": 1}},
    "12_repeat_with_break": {"first_negative": {**NONE, "loops": 1}},
    "13_for_step2": {"sum_even": {**NONE, "loops": 1}},
    "14_for_modifies_bound": {"shrink": {**NONE, "loops": 1}},
    "15_for_global_bound_with_call": {
        "tick": {"side_effects": "yes", "writes_globals": ["limit"], "reads_globals": ["limit"]},
        "run": {"side_effects": "unknown", "writes_globals": [], "reads_globals": ["limit"],
                "local": ["tick"], "loops": 1},
    },
    "16_continue": {"sum_odd": {**NONE, "loops": 1}},
    "17_global_write": {"bump": {"side_effects": "yes", "writes_globals": ["counter"],
                                 "reads_globals": ["counter"]}},
    "18_global_read_only": {"is_high": {**NONE, "reads_globals": ["threshold"]}},
    "19_pointer_param_write": {"set_five": {"side_effects": "yes",
                                            "through": [("p", "parameter", "yes")]}},
    "20_pointer_param_reassigned": {"maybe_write": {"side_effects": "unknown",
                                                    "through": [("p", "parameter", "may")]}},
    "21_array_param_write": {"clear_first": {"side_effects": "yes",
                                             "through": [("a", "parameter", "yes")]}},
    "22_struct_dot": {"make_x": {**NONE}},
    "23_struct_arrow": {"move_right": {"side_effects": "yes",
                                       "through": [("p", "parameter", "yes")]}},
    "24_static_state": {"next_id": {"side_effects": "yes", "state": ["count"], "writes_globals": []}},
    "25_printf_formats": {"report": {"side_effects": "yes", "external": ["printf"], "through": []}},
    "26_puts_putchar": {"banner": {"side_effects": "yes", "external": ["putchar", "puts"]}},
    "27_malloc_free": {"scratch": {"side_effects": "yes", "external": ["free", "malloc"],
                                   "through": [("buf", "local", "may")]}},
    "28_memset_param": {"wipe": {"side_effects": "yes", "external": ["memset"],
                                 "through": [("buf", "parameter", "yes")]}},
    "29_ternary_cast": {"half_of_max": {**NONE}},
    "30_logical_not": {"check": {**NONE, "returns": 2}},
    # Only changes the local r. (Named "unsupported" from when switch wasn't described yet.)
    "31_switch_unsupported": {"pick": {"side_effects": "none", "unsupported": 0}},
    "32_char_string_literals": {"ends_line": {**NONE}},
    "33_hex_octal": {"masks": {**NONE}},
    "34_calls_local": {
        "square": {**NONE},
        "sum_squares": {"side_effects": "unknown", "local": ["square"]},
    },
    "35_pass_pointer_to_local": {
        "reset": {"side_effects": "yes", "through": [("p", "parameter", "yes")]},
        "reset_twice": {"side_effects": "unknown", "local": ["reset"], "through": []},
    },
    # &age hands out age's location; that is a write (by scanf), not a read of its value.
    "36_scanf_global": {"read_age": {"side_effects": "yes", "writes_globals": ["age"],
                                     "reads_globals": [], "external": ["scanf"]}},
    "37_define_constant": {"fill_count": {**NONE, "loops": 1}},
    "38_nested_loops": {"find_pair": {**NONE, "loops": 2}},
    "39_sizeof": {"bytes_needed": {**NONE}},
    # stderr is a shared variable declared by stdio.h; reading it is a real read.
    "40_exit": {"must_be_positive": {"side_effects": "yes", "external": ["exit", "fprintf"],
                                     "reads_globals": ["stderr"]}},
    # A realistic multi-function file (the M1 final-test-plan check).
    "41_thermostat": {
        "add_reading": {"side_effects": "yes", "writes_globals": ["filled", "next_slot", "readings"],
                        "reads_globals": ["filled", "next_slot"], "through": []},
        "average": {**NONE, "reads_globals": ["filled", "readings"], "loops": 1},
        "control": {"side_effects": "yes", "writes_globals": ["heater_on"],
                    "reads_globals": ["heater_on"], "local": ["average"], "external": ["printf"]},
        "state_of": {**NONE, "reads_globals": ["heater_on"]},
        "clamp_all": {"side_effects": "yes", "through": [("values", "parameter", "yes")]},
    },
    # Programs written as ordinary C (not tailored to Plainsight), trial of 2026-09-27.
    "42_ringbuf": {
        "rb_init": {"side_effects": "yes", "through": [("rb", "parameter", "yes")]},
        "rb_push": {"side_effects": "yes", "through": [("rb", "parameter", "yes")], "returns": 2},
        "rb_pop": {"side_effects": "yes",
                   "through": [("out", "parameter", "yes"), ("rb", "parameter", "yes")]},
        "main": {"side_effects": "yes", "local": ["rb_init", "rb_pop", "rb_push"],
                 "external": ["printf"], "loops": 2},
    },
    "43_debounce": {
        "db_init": {"side_effects": "yes", "through": [("d", "parameter", "yes")]},
        "db_sample": {"side_effects": "yes", "through": [("d", "parameter", "yes")], "unsupported": 0},
        "main": {"side_effects": "yes", "local": ["db_init", "db_sample"], "loops": 1},
    },
    # isspace() is a macro in glibc; it is described (and counted) as the
    # standard function it stands for.
    "44_wordstats": {"main": {"side_effects": "yes", "unsupported": 0,
                              "external": ["getchar", "isspace", "printf"]}},
    # Regression cases for bugs found by running real-world code (cJSON, aes, sha256, linenoise).
    "45_partial_init": {"partial": {**NONE, "reads_globals": ["table"]}},
    "46_macro_facts": {
        # LOOKUP(i) is a user macro: shown as written, facts from its expansion (it reads codes).
        "decode": {**NONE, "reads_globals": ["codes"], "unsupported": 0},
        "set_flags": {"side_effects": "yes", "writes_globals": ["flags"], "reads_globals": []},
        "half_max": {**NONE},
        "run_callback": {"side_effects": "unknown", "unsupported": 1},
    },
    "47_switch_rules": {
        "classify": {**NONE, "unsupported": 0, "returns": 1},
        "count_high": {**NONE, "loops": 1},
        # IGNORE_ARG(s) doesn't use its argument, so it can't be matched to the
        # source exactly and stays a bracket; its expansion (the number 42) does nothing.
        "letters_first": {**NONE, "external": ["isalpha"], "unsupported": 1},
    },
    "48_loop_exits": {
        "find": {**NONE, "loops": 1, "returns": 2},
        "check_all": {"side_effects": "yes", "external": ["exit"]},
        "first_code": {**NONE, "loops": 1},
        "count_rows": {**NONE, "loops": 2},
        "pick": {"side_effects": "yes", "through": [("out", "parameter", "yes")]},
    },
    # The reading-test program (answers checked against the running program).
    "49_pump": {
        "pump_init": {"side_effects": "yes", "through": [("p", "parameter", "yes")]},
        "pump_step": {"side_effects": "yes", "through": [("p", "parameter", "yes")], "unsupported": 0},
        "minutes_to_fill": {**NONE, "loops": 1, "returns": 2},
    },
    "50_summary_guards": {
        "close_door": {"side_effects": "yes", "through": [("d", "parameter", "yes")]},
        "lock_door": {"side_effects": "yes", "through": [("d", "parameter", "yes")]},
        "bump": {"side_effects": "yes", "through": [("d", "parameter", "yes")]},
    },
    "51_crossing": {
        "crossing_tick": {"side_effects": "yes", "through": [("c", "parameter", "yes")], "unsupported": 0},
        "count_rush_hours": {**NONE, "loops": 1},
    },
    "52_tank_sim": {
        "report": {"side_effects": "yes", "writes_globals": ["total_faults"], "external": ["printf"]},
        "main": {"side_effects": "yes", "local": ["minutes_to_fill", "pump_init", "pump_step", "report"],
                 "loops": 1},
    },
    "53_calls_everywhere": {
        "sound_alarm": {"side_effects": "yes", "writes_globals": ["alarms"]},
        "sensor_ok": {**NONE},
        "machine_step": {"side_effects": "yes", "local": ["sound_alarm"],
                         "through": [("m", "parameter", "yes")]},
        "check": {"side_effects": "unknown", "local": ["sensor_ok"]},
    },
}
