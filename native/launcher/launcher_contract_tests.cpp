#include "launcher_contract.h"
#include <cstdlib>
#include <iostream>

namespace {
int checks = 0;
void Check(bool value, const char* message) {
    ++checks;
    if (!value) { std::cerr << "FAILED: " << message << '\n'; std::exit(1); }
}
std::string Replace(std::string value, const std::string& from, const std::string& to) {
    const auto position = value.find(from);
    Check(position != std::string::npos, "fixture substring exists");
    value.replace(position, from.size(), to);
    return value;
}
}
int main() {
    using namespace luma::launcher;
    const std::string hash(64, 'a'), exe_hash(64, 'b');
    const std::string id = "0.1.4-" + hash.substr(0, 24);
    const std::string pointer = "LUMA-ACTIVE-1\n" + id + "\n" + hash + "\n" + exe_hash + "\n";
    ActivePayload active;
    Check(ParsePointer(pointer, active), "valid pointer");
    Check(active.payload_id == id && active.version == "0.1.4" && active.bundle_sha256 == hash && active.executable_sha256 == exe_hash, "exact parsed fields");
    for (const auto& invalid : {std::string(), pointer + "\n", pointer.substr(0, pointer.size()-1),
             Replace(pointer, id, "../" + id), Replace(pointer, id, "C:\\" + id),
             Replace(pointer, id, id + ":stream"), Replace(pointer, "0.1.4", "00.1.4"),
             Replace(pointer, "0.1.4", "0.1"), Replace(pointer, "0.1.4", "0.1.4.0"),
             Replace(pointer, "0.1.4", "0.1.4/else"), Replace(pointer, hash, std::string(64, 'A')),
             Replace(pointer, exe_hash, std::string(64, 'z')), Replace(pointer, "LUMA-ACTIVE-1", "LUMA-ACTIVE-2"),
             Replace(pointer, "\n", "\r\n"), std::string("\xef\xbb\xbf") + pointer,
             Replace(pointer, id, "1.2.3-" + std::string(24, 'f')), std::string(513, 'a')}) {
        ActivePayload unused;
        Check(!ParsePointer(invalid, unused), "unsafe pointer rejected");
    }
    const std::string metadata = "{\n  \"managed_install\": true, \"layout\": \"versions-v1\", \"version\": \"0.1.4\",\n\"payload_id\": \"" + id + "\", \"bundle_sha256\": \"" + hash + "\"\n}";
    Check(ValidateMetadata(metadata, active), "generated metadata accepted");
    for (const auto& invalid : {metadata + "{}", Replace(metadata, "true", "false"),
             Replace(metadata, "true", "\"true\""), Replace(metadata, "versions-v1", "versions-v2"),
             Replace(metadata, "0.1.4", "0.1.3"), Replace(metadata, hash, std::string(64, 'f')),
             Replace(metadata, "{", "{\"managed_install\":true,"), Replace(metadata, "{", "{\"extra\":\"value\","),
             Replace(metadata, "\"layout\"", "\"lay\\u006fut\""), Replace(metadata, "true", "truex"),
             Replace(metadata, "\"versions-v1\"", "null"), std::string(4097, ' '), metadata.substr(0, metadata.size()-1)})
        Check(!ValidateMetadata(invalid, active), "mismatched metadata rejected");
    std::string owner;
    Check(ParseOwner("LUMA-LAUNCHER-1\n" + hash + "\n", owner) && owner == hash, "valid owner");
    Check(!ParseOwner("LUMA-LAUNCHER-1\n" + hash, owner), "owner final LF required");
    Check(!ParseOwner("LUMA-LAUNCHER-2\n" + hash + "\n", owner), "owner protocol rejected");
    Check(!ParseOwner("LUMA-LAUNCHER-1\n" + hash + "\n\n", owner), "owner extra data rejected");
    Check(QuoteArgument(L"") == L"\"\"", "empty argument");
    Check(QuoteArgument(L"a b") == L"\"a b\"", "spaces quoted");
    Check(QuoteArgument(L"a\"b") == L"\"a\\\"b\"", "embedded quote escaped");
    Check(QuoteArgument(L"C:\\path\\") == L"\"C:\\path\\\\\"", "trailing slash doubled");
    Check(QuoteArgument(L"a\\\"b") == L"\"a\\\\\\\"b\"", "slash before quote doubled");
    Check(CommandLine(L"C:\\Luma app\\Luma.exe", {L"--update-startup-port", L"1234", L""}) ==
          L"\"C:\\Luma app\\Luma.exe\" \"--update-startup-port\" \"1234\" \"\"", "command composed");
    std::cout << "PASS: " << checks << " launcher contract checks\n";
    return 0;
}
