// SPDX-License-Identifier: GPL-2.0+ OR BSD-3-Clause
// Tier-2.5 differential oracle: C original vs Rust translation,
// error_private (zstd). Reference extracted from
// lib/zstd/common/error_private.c (v7.1), kept byte-identical to the
// #else (non-ZSTD_STRIP_ERROR_STRINGS) branch, since that's the live
// path in this kernel's .config.
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>

typedef enum {
  no_error = 0,
  GENERIC  = 1,
  prefix_unknown                = 10,
  version_unsupported           = 12,
  frameParameter_unsupported    = 14,
  frameParameter_windowTooLarge = 16,
  corruption_detected = 20,
  checksum_wrong      = 22,
  literals_headerWrong = 24,
  dictionary_corrupted      = 30,
  dictionary_wrong          = 32,
  dictionaryCreation_failed = 34,
  parameter_unsupported   = 40,
  parameter_combination_unsupported = 41,
  parameter_outOfBound    = 42,
  tableLog_tooLarge       = 44,
  maxSymbolValue_tooLarge = 46,
  maxSymbolValue_tooSmall = 48,
  cannotProduce_uncompressedBlock = 49,
  stabilityCondition_notRespected = 50,
  stage_wrong       = 60,
  init_missing      = 62,
  memory_allocation = 64,
  workSpace_tooSmall= 66,
  dstSize_tooSmall = 70,
  srcSize_wrong    = 72,
  dstBuffer_null   = 74,
  noForwardProgress_destFull = 80,
  noForwardProgress_inputEmpty = 82,
  frameIndex_tooLarge = 100,
  seekableIO          = 102,
  dstBuffer_wrong     = 104,
  srcBuffer_wrong     = 105,
  sequenceProducer_failed = 106,
  externalSequences_invalid = 107,
  maxCode = 120
} test_enum;

static const char* ERR_getErrorString(test_enum code)
{
    static const char* const notErrorCode = "Unspecified error code";
    switch( code )
    {
    case no_error: return "No error detected";
    case GENERIC:  return "Error (generic)";
    case prefix_unknown: return "Unknown frame descriptor";
    case version_unsupported: return "Version not supported";
    case frameParameter_unsupported: return "Unsupported frame parameter";
    case frameParameter_windowTooLarge: return "Frame requires too much memory for decoding";
    case corruption_detected: return "Data corruption detected";
    case checksum_wrong: return "Restored data doesn't match checksum";
    case literals_headerWrong: return "Header of Literals' block doesn't respect format specification";
    case parameter_unsupported: return "Unsupported parameter";
    case parameter_combination_unsupported: return "Unsupported combination of parameters";
    case parameter_outOfBound: return "Parameter is out of bound";
    case init_missing: return "Context should be init first";
    case memory_allocation: return "Allocation error : not enough memory";
    case workSpace_tooSmall: return "workSpace buffer is not large enough";
    case stage_wrong: return "Operation not authorized at current processing stage";
    case tableLog_tooLarge: return "tableLog requires too much memory : unsupported";
    case maxSymbolValue_tooLarge: return "Unsupported max Symbol Value : too large";
    case maxSymbolValue_tooSmall: return "Specified maxSymbolValue is too small";
    case cannotProduce_uncompressedBlock: return "This mode cannot generate an uncompressed block";
    case stabilityCondition_notRespected: return "pledged buffer stability condition is not respected";
    case dictionary_corrupted: return "Dictionary is corrupted";
    case dictionary_wrong: return "Dictionary mismatch";
    case dictionaryCreation_failed: return "Cannot create Dictionary from provided samples";
    case dstSize_tooSmall: return "Destination buffer is too small";
    case srcSize_wrong: return "Src size is incorrect";
    case dstBuffer_null: return "Operation on NULL destination buffer";
    case noForwardProgress_destFull: return "Operation made no progress over multiple calls, due to output buffer being full";
    case noForwardProgress_inputEmpty: return "Operation made no progress over multiple calls, due to input being empty";
    case frameIndex_tooLarge: return "Frame index is too large";
    case seekableIO: return "An I/O error occurred when reading/seeking";
    case dstBuffer_wrong: return "Destination buffer is wrong";
    case srcBuffer_wrong: return "Source buffer is wrong";
    case sequenceProducer_failed: return "Block-level external sequence producer returned an error code";
    case externalSequences_invalid: return "External sequences are not valid";
    case maxCode:
    default: return notErrorCode;
    }
}

// Real, known enum values (all 35 cases including maxCode).
static const int known_codes[] = {
    0, 1, 10, 12, 14, 16, 20, 22, 24, 30, 32, 34, 40, 41, 42, 44, 46, 48,
    49, 50, 60, 62, 64, 66, 70, 72, 74, 80, 82, 100, 102, 104, 105, 106,
    107, 120,
};

// Explicit LCG (same constants used across all bench/diff_*.c files).
static uint64_t lcg_state;
static uint32_t lcg_next(void)
{
	lcg_state = lcg_state * 6364136223846793005ULL + 1442695040888963407ULL;
	return (uint32_t)(lcg_state >> 32);
}

int main(int argc, char **argv)
{
	long n = argc > 1 ? atol(argv[1]) : 5000;
	lcg_state = argc > 2 ? (uint64_t)atol(argv[2]) : 271828;
	int nknown = (int)(sizeof(known_codes) / sizeof(known_codes[0]));

	for (long i = 0; i < n; i++) {
		int code;
		// Half known-real codes, half arbitrary (mostly hits default).
		if (lcg_next() % 2 == 0) {
			code = known_codes[lcg_next() % nknown];
		} else {
			code = (int)(lcg_next() % 200) - 50;
		}
		const char *s = ERR_getErrorString((test_enum)code);
		printf("code,%d,%s\n", code, s);
	}
	return 0;
}
