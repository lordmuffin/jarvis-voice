@rem Gradle startup script for Windows

@if "%DEBUG%"=="" @echo off
@rem Set local scope for the variables with windows NT shell
if "%OS%"=="Windows_NT" setlocal

set DIRNAME=%~dp0
if "%DIRNAME%"=="" set DIRNAME=.
set APP_HOME=%DIRNAME%

set CLASSPATH=%APP_HOME%\gradle\wrapper\gradle-wrapper.jar

if not exist "%CLASSPATH%" (
    echo Gradle wrapper jar not found. Please run 'gradle wrapper' from this directory.
    exit /B 1
)

@rem Find java.exe
if defined JAVA_HOME (
    set JAVA_EXE=%JAVA_HOME%/bin/java.exe
) else (
    set JAVA_EXE=java.exe
)

"%JAVA_EXE%" -cp "%CLASSPATH%" org.gradle.wrapper.GradleWrapperMain %*

:end
@rem End local scope for the variables with windows NT shell
if "%ERRORLEVEL%"=="0" goto mainEnd

:fail
exit /b 1

:mainEnd
if "%OS%"=="Windows_NT" endlocal

:omega
