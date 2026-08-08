# API

Generated from the docstrings in `src/tlakit`. For environment variables and
notebook magics — which have no docstring to be generated from — see
[Reference](reference.md).

## Entry points

::: tlakit.api
    options:
      members:
        - load
        - check_source
        - Spec
        - build_config
        - default_runner
        - use_remote
        - use_local
        - module_name_of
        - tla_value

## Results

::: tlakit.result
    options:
      members:
        - CheckResult
        - Outcome
        - Trace
        - Action
        - Diagnostic
        - Severity
        - Stats
        - Coverage
        - RawOutput
        - flatten_state

## Runners

::: tlakit.cli
    options:
      members:
        - CliRunner
        - EvalResult
        - java_executable
        - JavaNotFound

::: tlakit.remote
    options:
      members:
        - RemoteRunner
        - RemoteError
        - Unsupported

## Locating the toolchain

::: tlakit.jar
    options:
      members:
        - find_tools_jar
        - find_community_jar
        - assert_isolated
        - JarNotFound

::: tlakit.install

## Parameter sweeps

::: tlakit.sweep

## Traces

::: tlakit.trace
    options:
      members:
        - load_trace
        - parse_text_trace
        - parse_tla_value
        - TlaValueError

## Symbols: completion and hover

::: tlakit.symbols
    options:
      members:
        - Symbol
        - definitions
        - extends
        - symbols_in_scope
        - session_scope
        - standard_modules
        - complete
        - describe
        - word_at

## Apalache: the symbolic checker

::: tlakit.apalache
    options:
      members:
        - ApalacheRunner
        - find_apalache
        - trace_from_itf
        - itf_value
        - ApalacheNotFound

## Stepping under the TLA+ Debugger

::: tlakit.dap
    options:
      members:
        - DebugSession
        - Step
        - walk
        - next_relation
        - relation_lines
        - DapClient
        - DebuggerError
        - DebuggerTimeout

## Notebook rendering

::: tlakit.render
    options:
      members:
        - result_html
        - TraceView
        - trace_view
        - stepper_view

## Magics

::: tlakit.magics
    options:
      members:
        - TlaMagics
        - TlaMagicError
