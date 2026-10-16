#pragma once

// -1: this process is verified and may call TestSurrogate.
// >=0: return the completed child's status or the launcher failure.
int PrepareSurrogateClient(const wchar_t* inputPath, bool childMode);
