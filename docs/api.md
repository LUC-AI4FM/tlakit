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

## Notebook rendering

::: tlakit.render
    options:
      members:
        - result_html
        - TraceView
        - trace_view

## Magics

::: tlakit.magics
    options:
      members:
        - TlaMagics
        - TlaMagicError
