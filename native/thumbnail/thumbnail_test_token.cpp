#include <windows.h>
#include <sddl.h>
#include <climits>
#include <cstdio>
#include <cwchar>
#include <new>
#include <string>
#include <utility>
#include <vector>

#include "thumbnail_test_token.h"

// Test support only. This code is not linked into LumaThumbnail.dll.
namespace {
struct Failure { const wchar_t* operation; DWORD code; };
[[noreturn]] void Fail(const wchar_t* operation, DWORD code) {
    throw Failure{operation, code};
}
void Check(BOOL success, const wchar_t* operation) {
    if (!success) Fail(operation, GetLastError());
}
void Report(const wchar_t* operation, DWORD code) noexcept {
    wchar_t message[512]{};
    FormatMessageW(FORMAT_MESSAGE_FROM_SYSTEM | FORMAT_MESSAGE_IGNORE_INSERTS,
                   nullptr, code, 0, message, ARRAYSIZE(message), nullptr);
    std::fwprintf(stderr, L"%ls: Win32=%lu (0x%08lX) %ls\n",
                  operation, code, code, message);
    std::fflush(stderr);
}
struct Handle {
    HANDLE value = nullptr;
    Handle() = default;
    explicit Handle(HANDLE h) noexcept : value(h) {}
    Handle(const Handle&) = delete;
    Handle& operator=(const Handle&) = delete;
    Handle(Handle&& other) noexcept
        : value(std::exchange(other.value, nullptr)) {}
    ~Handle() {
        if (value && value != INVALID_HANDLE_VALUE) CloseHandle(value);
    }
};
template<class T>
T Query(HANDLE token, TOKEN_INFORMATION_CLASS kind, const wchar_t* operation) {
    T result{};
    DWORD bytes = 0;
    Check(GetTokenInformation(token, kind, &result, sizeof(result), &bytes),
          operation);
    if (bytes != sizeof(result)) Fail(operation, ERROR_INVALID_DATA);
    return result;
}
std::vector<BYTE> QueryBuffer(HANDLE token, TOKEN_INFORMATION_CLASS kind,
                              const wchar_t* operation) {
    DWORD bytes = 0;
    SetLastError(ERROR_SUCCESS);
    const BOOL first = GetTokenInformation(token, kind, nullptr, 0, &bytes);
    const DWORD error = GetLastError();
    if (first || error != ERROR_INSUFFICIENT_BUFFER || !bytes)
        Fail(operation, first ? ERROR_INVALID_DATA : error);
    std::vector<BYTE> buffer(bytes);
    Check(GetTokenInformation(token, kind, buffer.data(), bytes, &bytes),
          operation);
    return buffer;
}
Handle ProcessToken(DWORD access) {
    Handle token;
    Check(OpenProcessToken(GetCurrentProcess(), access, &token.value),
          L"OpenProcessToken");
    return token;
}
void RejectThreadImpersonation() {
    Handle token;
    if (OpenThreadToken(GetCurrentThread(), TOKEN_QUERY, TRUE, &token.value))
        Fail(L"policy: thread impersonation is not permitted",
             ERROR_BAD_IMPERSONATION_LEVEL);
    const DWORD error = GetLastError();
    if (error != ERROR_NO_TOKEN) Fail(L"OpenThreadToken", error);
}
struct TokenState {
    std::wstring userSid;
    bool serviceIdentity = false;
    DWORD elevated = 0;
    TOKEN_ELEVATION_TYPE elevationType = TokenElevationTypeDefault;
    DWORD integrity = 0;
    DWORD hasRestrictions = 0;
    BOOL restrictedSids = FALSE;
    bool enabledAdministrators = false;
    DWORD session = 0;
};
TokenState Inspect(HANDLE token, const wchar_t* label) {
    TokenState result;
    const auto user = QueryBuffer(token, TokenUser, L"TokenUser");
    const PSID userSid = reinterpret_cast<const TOKEN_USER*>(user.data())->User.Sid;
    if (!IsValidSid(userSid)) Fail(L"TokenUser SID", ERROR_INVALID_SID);
    LPWSTR userText = nullptr;
    Check(ConvertSidToStringSidW(userSid, &userText), L"ConvertSidToStringSidW");
    try { result.userSid = userText; }
    catch (...) { LocalFree(userText); throw; }
    LocalFree(userText);
    result.serviceIdentity =
        IsWellKnownSid(userSid, WinLocalSystemSid) ||
        IsWellKnownSid(userSid, WinLocalServiceSid) ||
        IsWellKnownSid(userSid, WinNetworkServiceSid);
    result.elevated = Query<TOKEN_ELEVATION>(
        token, TokenElevation, L"TokenElevation").TokenIsElevated;
    result.elevationType = Query<TOKEN_ELEVATION_TYPE>(
        token, TokenElevationType, L"TokenElevationType");
    // Microsoft documents DWORD, but some Windows builds report a different
    // ReturnLength. This is telemetry, never a substitute for Safe() checks.
    DWORD restrictionValue = 0, restrictionBytes = 0;
    const BOOL restrictionOk = GetTokenInformation(
        token, TokenHasRestrictions, &restrictionValue,
        sizeof(restrictionValue), &restrictionBytes);
    const DWORD restrictionError = restrictionOk ? ERROR_SUCCESS : GetLastError();
    result.hasRestrictions = restrictionOk &&
        (restrictionBytes == sizeof(BOOLEAN) || restrictionBytes == sizeof(DWORD))
            ? (restrictionValue != 0 ? 1u : 0u) : MAXDWORD;
    std::fwprintf(stderr,
        L"TokenHasRestrictions diagnostic: success=%d error=%lu bytes=%lu "
        L"value=%lu (4294967295=unknown)\n",
        restrictionOk, restrictionError, restrictionBytes, result.hasRestrictions);
    result.session = Query<DWORD>(token, TokenSessionId, L"TokenSessionId");
    const auto integrity = QueryBuffer(
        token, TokenIntegrityLevel, L"TokenIntegrityLevel");
    const PSID integritySid =
        reinterpret_cast<const TOKEN_MANDATORY_LABEL*>(integrity.data())->Label.Sid;
    if (!IsValidSid(integritySid) || !*GetSidSubAuthorityCount(integritySid))
        Fail(L"TokenIntegrityLevel SID", ERROR_INVALID_SID);
    result.integrity = *GetSidSubAuthority(
        integritySid, *GetSidSubAuthorityCount(integritySid) - 1);

    alignas(DWORD) BYTE adminSid[SECURITY_MAX_SID_SIZE]{};
    DWORD adminBytes = sizeof(adminSid);
    Check(CreateWellKnownSid(WinBuiltinAdministratorsSid, nullptr,
                             adminSid, &adminBytes), L"Administrators SID");
    const auto groups = QueryBuffer(token, TokenGroups, L"TokenGroups");
    const auto* tokenGroups = reinterpret_cast<const TOKEN_GROUPS*>(groups.data());
    for (DWORD i = 0; i != tokenGroups->GroupCount; ++i) {
        const auto& group = tokenGroups->Groups[i];
        if (EqualSid(group.Sid, adminSid) &&
            (group.Attributes & SE_GROUP_ENABLED) &&
            !(group.Attributes & SE_GROUP_USE_FOR_DENY_ONLY))
            result.enabledAdministrators = true;
    }
    SetLastError(ERROR_SUCCESS);
    result.restrictedSids = IsTokenRestricted(token);
    const DWORD error = GetLastError();
    if (!result.restrictedSids && error != ERROR_SUCCESS)
        Fail(L"IsTokenRestricted", error);
    DWORD processSession = 0;
    Check(ProcessIdToSessionId(GetCurrentProcessId(), &processSession),
          L"ProcessIdToSessionId");
    DWORD enableLua = 0, bytes = sizeof(enableLua);
    const LSTATUS luaStatus = RegGetValueW(
        HKEY_LOCAL_MACHINE,
        L"SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Policies\\System",
        L"EnableLUA", RRF_RT_REG_DWORD | RRF_SUBKEY_WOW6464KEY,
        nullptr, &enableLua, &bytes);
    std::fwprintf(stderr,
        L"[%ls] user=%ls pid=%lu bits=%u processSession=%lu tokenSession=%lu "
        L"TokenElevation=%lu TokenElevationType=%u IntegrityRID=0x%lX "
        L"TokenHasRestrictions=%lu IsTokenRestricted=%d enabledAdministrators=%d "
        L"EnableLUA_status=%ld EnableLUA=%lu\n",
        label, result.userSid.c_str(), GetCurrentProcessId(),
        static_cast<unsigned>(sizeof(void*) * 8),
        processSession, result.session, result.elevated,
        static_cast<unsigned>(result.elevationType), result.integrity,
        result.hasRestrictions, result.restrictedSids,
        result.enabledAdministrators ? 1 : 0, luaStatus, enableLua);
    std::fflush(stderr);
    return result;
}
bool Safe(const TokenState& state) {
    return !state.serviceIdentity && state.elevated == 0 &&
           state.integrity == SECURITY_MANDATORY_MEDIUM_RID &&
           !state.enabledAdministrators;
}
bool EnvironmentTrue(const wchar_t* name) {
    wchar_t value[16]{};
    const DWORD length = GetEnvironmentVariableW(name, value, ARRAYSIZE(value));
    return length && length < ARRAYSIZE(value) &&
        (_wcsicmp(value, L"true") == 0 || std::wcscmp(value, L"1") == 0);
}
std::wstring Quote(const std::wstring& value) {
    std::wstring result = L"\"";
    size_t slashes = 0;
    for (wchar_t c : value) {
        if (c == L'\\') { ++slashes; continue; }
        result.append(c == L'"' ? slashes * 2 + 1 : slashes, L'\\');
        result += c;
        slashes = 0;
    }
    result.append(slashes * 2, L'\\');
    return result + L'"';
}
std::wstring ExecutablePath() {
    std::vector<wchar_t> path(32768);
    const DWORD count = GetModuleFileNameW(
        nullptr, path.data(), static_cast<DWORD>(path.size()));
    if (!count) Fail(L"GetModuleFileNameW", GetLastError());
    if (count >= path.size()) Fail(L"Executable path truncated", ERROR_INSUFFICIENT_BUFFER);
    return std::wstring(path.data(), count);
}
Handle DuplicateStandardHandle(DWORD which, const wchar_t* operation) {
    const HANDLE source = GetStdHandle(which);
    if (!source || source == INVALID_HANDLE_VALUE)
        Fail(operation, ERROR_INVALID_HANDLE);
    Handle duplicate;
    Check(DuplicateHandle(GetCurrentProcess(), source, GetCurrentProcess(),
                          &duplicate.value, 0, TRUE, DUPLICATE_SAME_ACCESS),
          operation);
    return duplicate;
}
struct AttributeList {
    std::vector<BYTE> storage;
    LPPROC_THREAD_ATTRIBUTE_LIST value = nullptr;
    AttributeList() {
        SIZE_T bytes = 0;
        SetLastError(ERROR_SUCCESS);
        const BOOL first = InitializeProcThreadAttributeList(nullptr, 1, 0, &bytes);
        const DWORD error = GetLastError();
        if (first || error != ERROR_INSUFFICIENT_BUFFER || !bytes)
            Fail(L"InitializeProcThreadAttributeList(size)",
                 first ? ERROR_INVALID_DATA : error);
        storage.resize(bytes);
        auto* candidate =
            reinterpret_cast<LPPROC_THREAD_ATTRIBUTE_LIST>(storage.data());
        Check(InitializeProcThreadAttributeList(candidate, 1, 0, &bytes),
              L"InitializeProcThreadAttributeList");
        value = candidate;
    }
    ~AttributeList() { if (value) DeleteProcThreadAttributeList(value); }
};
struct ChildGuard {
    HANDLE process;
    HANDLE job;
    bool assigned = false;
    bool finished = false;
    ~ChildGuard() {
        if (finished) return;
        bool terminated = false;
        if (assigned) {
            terminated = TerminateJobObject(job, ERROR_PROCESS_ABORTED) != FALSE;
            if (!terminated) Report(L"TerminateJobObject(cleanup)", GetLastError());
        }
        if (!terminated && !TerminateProcess(process, ERROR_PROCESS_ABORTED))
            Report(L"TerminateProcess(owned child cleanup)", GetLastError());
        const DWORD wait = WaitForSingleObject(process, 1000);
        if (wait != WAIT_OBJECT_0)
            Report(L"WaitForSingleObject(cleanup)",
                   wait == WAIT_FAILED ? GetLastError() : ERROR_TIMEOUT);
    }
};
int Spawn(HANDLE token, const wchar_t* inputPath) {
    const std::wstring executable = ExecutablePath();
    const std::wstring command =
        Quote(executable) + L" --surrogate-child " + Quote(inputPath);
    if (command.size() >= 32767)
        Fail(L"Child command line too long", ERROR_FILENAME_EXCED_RANGE);
    std::vector<wchar_t> commandBuffer(command.begin(), command.end());
    commandBuffer.push_back(L'\0');
    Handle out = DuplicateStandardHandle(STD_OUTPUT_HANDLE, L"DuplicateHandle(stdout)");
    Handle err = DuplicateStandardHandle(STD_ERROR_HANDLE, L"DuplicateHandle(stderr)");
    SECURITY_ATTRIBUTES inheritable{sizeof(SECURITY_ATTRIBUTES), nullptr, TRUE};
    Handle in(CreateFileW(L"NUL", GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE,
                          &inheritable, OPEN_EXISTING, 0, nullptr));
    if (in.value == INVALID_HANDLE_VALUE) Fail(L"CreateFileW(NUL)", GetLastError());
    HANDLE inherited[] = {in.value, out.value, err.value};
    AttributeList attributes;
    Check(UpdateProcThreadAttribute(
              attributes.value, 0, PROC_THREAD_ATTRIBUTE_HANDLE_LIST,
              inherited, sizeof(inherited), nullptr, nullptr),
          L"UpdateProcThreadAttribute(HANDLE_LIST)");
    Handle job(CreateJobObjectW(nullptr, nullptr));
    if (!job.value) Fail(L"CreateJobObjectW", GetLastError());
    JOBOBJECT_EXTENDED_LIMIT_INFORMATION limits{};
    limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
    Check(SetInformationJobObject(job.value, JobObjectExtendedLimitInformation,
                                  &limits, sizeof(limits)),
          L"SetInformationJobObject(KILL_ON_JOB_CLOSE)");
    STARTUPINFOEXW startup{};
    startup.StartupInfo.cb = sizeof(startup);
    startup.StartupInfo.dwFlags = STARTF_USESTDHANDLES;
    startup.StartupInfo.hStdInput = in.value;
    startup.StartupInfo.hStdOutput = out.value;
    startup.StartupInfo.hStdError = err.value;
    startup.lpAttributeList = attributes.value;
    PROCESS_INFORMATION information{};
    std::fflush(nullptr);
    Check(CreateProcessAsUserW(
              token, executable.c_str(), commandBuffer.data(), nullptr, nullptr,
              TRUE, CREATE_SUSPENDED | EXTENDED_STARTUPINFO_PRESENT,
              nullptr, nullptr, &startup.StartupInfo, &information),
          L"CreateProcessAsUserW");
    Handle process(information.hProcess);
    Handle thread(information.hThread);
    ChildGuard guard{process.value, job.value};
    Check(AssignProcessToJobObject(job.value, process.value),
          L"AssignProcessToJobObject");
    guard.assigned = true;
    const DWORD previous = ResumeThread(thread.value);
    if (previous == static_cast<DWORD>(-1)) Fail(L"ResumeThread", GetLastError());
    if (previous != 1) Fail(L"Unexpected suspend count", ERROR_INVALID_STATE);
    // Leave one second for cleanup within the 45-second launcher bound.
    const DWORD wait = WaitForSingleObject(process.value, 44000);
    if (wait != WAIT_OBJECT_0)
        Fail(L"WaitForSingleObject(child)",
             wait == WAIT_FAILED ? GetLastError() : ERROR_TIMEOUT);
    DWORD exitCode = 1;
    Check(GetExitCodeProcess(process.value, &exitCode), L"GetExitCodeProcess");
    guard.finished = true;
    std::fwprintf(stderr, L"surrogate child pid=%lu exit=%lu (0x%08lX)\n",
                  information.dwProcessId, exitCode, exitCode);
    std::fflush(stderr);
    return exitCode <= static_cast<DWORD>(INT_MAX) ? static_cast<int>(exitCode) : 1;
}
} // namespace

int PrepareSurrogateClient(const wchar_t* inputPath, bool childMode) {
    try {
        if (!inputPath || !*inputPath) Fail(L"Input path", ERROR_INVALID_PARAMETER);
        RejectThreadImpersonation();
        Handle current = ProcessToken(TOKEN_QUERY);
        const TokenState state =
            Inspect(current.value, childMode ? L"surrogate-child" : L"surrogate");
        if (state.serviceIdentity)
            Fail(L"Service identities are outside this CI test", ERROR_ACCESS_DENIED);
        if (Safe(state)) return -1;
        if (childMode)
            Fail(L"Child is not unelevated medium/non-admin", ERROR_ACCESS_DENIED);
        if (!EnvironmentTrue(L"CI") && !EnvironmentTrue(L"GITHUB_ACTIONS"))
            Fail(L"Token reduction is permitted only in CI", ERROR_ACCESS_DENIED);
        if (state.integrity < SECURITY_MANDATORY_MEDIUM_RID)
            Fail(L"Integrity must never be raised", ERROR_ACCESS_DENIED);

        Handle source = ProcessToken(TOKEN_QUERY | TOKEN_DUPLICATE |
                                     TOKEN_ASSIGN_PRIMARY | TOKEN_ADJUST_DEFAULT);
        alignas(DWORD) BYTE adminSid[SECURITY_MAX_SID_SIZE]{};
        DWORD adminBytes = sizeof(adminSid);
        Check(CreateWellKnownSid(WinBuiltinAdministratorsSid, nullptr,
                                 adminSid, &adminBytes), L"Administrators SID");
        SID_AND_ATTRIBUTES denyAdmin{adminSid, 0};
        Handle restricted;
        // LUA_TOKEN requests a limited-user token, not just removed privileges.
        // Verify actual elevation/integrity instead of assuming this succeeded.
        Check(CreateRestrictedToken(source.value, DISABLE_MAX_PRIVILEGE | LUA_TOKEN,
                                    1, &denyAdmin, 0, nullptr, 0, nullptr,
                                    &restricted.value), L"CreateRestrictedToken");
        if (Query<TOKEN_TYPE>(restricted.value, TokenType, L"TokenType") != TokenPrimary)
            Fail(L"Restricted token is not primary", ERROR_BAD_TOKEN_TYPE);
        struct MediumLabel {
            TOKEN_MANDATORY_LABEL label{};
            alignas(DWORD) BYTE sid[SECURITY_MAX_SID_SIZE]{};
        } medium;
        DWORD mediumBytes = sizeof(medium.sid);
        Check(CreateWellKnownSid(WinMediumLabelSid, nullptr, medium.sid, &mediumBytes),
              L"Medium SID");
        medium.label.Label.Sid = medium.sid;
        medium.label.Label.Attributes = SE_GROUP_INTEGRITY;
        Check(SetTokenInformation(
                  restricted.value, TokenIntegrityLevel, &medium.label,
                  static_cast<DWORD>(sizeof(TOKEN_MANDATORY_LABEL)) +
                      GetLengthSid(medium.sid)), L"SetTokenInformation(Medium)");
        const TokenState candidate = Inspect(restricted.value, L"candidate");
        if (!Safe(candidate) || candidate.session != state.session ||
            candidate.userSid != state.userSid)
            Fail(L"Reduced token failed verification", ERROR_ACCESS_DENIED);
        return Spawn(restricted.value, inputPath);
    } catch (const Failure& failure) {
        Report(failure.operation, failure.code);
    } catch (const std::bad_alloc&) {
        Report(L"Allocation", ERROR_NOT_ENOUGH_MEMORY);
    } catch (...) {
        Report(L"Unexpected test-launcher exception", ERROR_UNHANDLED_EXCEPTION);
    }
    return 1;
}
