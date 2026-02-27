// Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

// checkpoint_chaincode_test.go — reference unit tests for checkpoint_chaincode.go.
//
// This file is a DOCUMENTATION TEMPLATE that mirrors
// fabric_chaincode/checkpoint_chaincode_test.go and documents the correct
// external-facing API for the checkpoint chaincode.
//
// The canonical, runnable unit tests live in
//   self_fixing_engineer/fabric_chaincode/checkpoint_chaincode_test.go
//
// To run these tests, place this file in the fabric_chaincode/ directory
// (where the main package lives) and run:
//   go test -v -count=1 ./...
//
// API change notes (relative to original design):
//   - retry-go/v4 removed: GetState failures in chaincode are non-transient
//   - proto import: github.com/golang/protobuf/proto (not google.golang.org)
//     because fabric-protos-go v0.3.x uses legacy proto2 generated types
//   - ReadCheckpoint: explicit string param, not variadic ("" = latest)
//   - Logging env var: CORE_CHAINCODE_LOGGING_SHIM (not CORE_CHAINCODE_LOGGING_LEVEL)
//   - Logger levels: levelInfo/levelDebug constants (shim.LogInfo/LogDebug removed)
//   - Off-chain refs: path-style only ("bucket/key"); colons/double-slashes disallowed
//   - Timestamps: derived from GetTxTimestamp() — deterministic, not time.Now()

package main_test

import (
"encoding/json"
"fmt"
"os"
"testing"

// fabric-protos-go v0.3.x uses legacy proto2 generated types; the
// github.com/golang/protobuf package is intentionally used here.
"github.com/golang/protobuf/proto"
"github.com/hyperledger/fabric-contract-api-go/contractapi"
"github.com/hyperledger/fabric-contract-api-go/mocks"
"github.com/hyperledger/fabric-protos-go/msp"
"github.com/hyperledger/fabric-protos-go/peer"
"github.com/stretchr/testify/assert"
"github.com/stretchr/testify/require"
)

// Import the chaincode package — only valid when this file is placed in
// the fabric_chaincode/ directory alongside the source.
import . "checkpoint_chaincode"

// ---------------------------------------------------------------------------
// Test constants
// ---------------------------------------------------------------------------

const (
testName        = "test_checkpoint"
// Valid 64-character SHA-256 hex strings.
testDataHash    = "abcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890"
testPrevHash    = "1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef"
testMetadata    = `{"key":"value"}`
testOffChainRef = "bucket/object-key" // Path-style; colons/double-slashes disallowed
testMessage     = "Rollback test message"
testMSPID       = "admin"
testTxID        = "test_tx_id"
testEpochSec    = int64(1627849200)
testTimestampMs = testEpochSec * 1000
)

// ---------------------------------------------------------------------------
// Mock setup
// ---------------------------------------------------------------------------

// setupMockContext returns a mock TransactionContext pre-configured with a
// serialized admin MSP identity and a deterministic transaction timestamp.
func setupMockContext(t testing.TB) *mocks.TransactionContext {
t.Helper()
ctx := new(mocks.TransactionContext)
stub := new(mocks.ChaincodeStub)
ctx.GetStubReturns(stub)

// Serialize MSP identity using github.com/golang/protobuf/proto
// (not google.golang.org/protobuf/proto — fabric-protos-go v0.3.x compatibility)
sid := &msp.SerializedIdentity{Mspid: testMSPID}
creatorBytes, err := proto.Marshal(sid)
require.NoError(t, err)
stub.GetCreatorReturns(creatorBytes, nil)

stub.GetTxIDReturns(testTxID)
// Timestamp uses github.com/golang/protobuf/ptypes/timestamp (via peer.Timestamp alias)
stub.GetTxTimestampReturns(&peer.Timestamp{Seconds: testEpochSec, Nanos: 0}, nil)

return ctx
}

// ---------------------------------------------------------------------------
// InitLogger
// ---------------------------------------------------------------------------

// TestInitLogger verifies logger level initialisation.
// Note: the env var changed from CORE_CHAINCODE_LOGGING_LEVEL to
// CORE_CHAINCODE_LOGGING_SHIM; level constants are levelInfo/levelDebug (not
// shim.LogInfo/shim.LogDebug, which were removed with the shim logger).
func TestInitLogger(t *testing.T) {
t.Run("Default INFO", func(t *testing.T) {
os.Unsetenv("CORE_CHAINCODE_LOGGING_SHIM")
InitLogger()
assert.Equal(t, levelInfo, logger.GetLevel())
})

t.Run("DEBUG Level", func(t *testing.T) {
t.Setenv("CORE_CHAINCODE_LOGGING_SHIM", "DEBUG")
InitLogger()
assert.Equal(t, levelDebug, logger.GetLevel())
})
}

// ---------------------------------------------------------------------------
// InitLedger / HealthCheck
// ---------------------------------------------------------------------------

func TestInitLedger(t *testing.T) {
err := (&SmartContract{}).InitLedger(setupMockContext(t))
assert.NoError(t, err)
}

func TestHealthCheck(t *testing.T) {
result, err := (&SmartContract{}).HealthCheck(setupMockContext(t))
require.NoError(t, err)
assert.Equal(t, "Chaincode is healthy", result)
}

// ---------------------------------------------------------------------------
// WriteCheckpoint — success paths
// ---------------------------------------------------------------------------

func TestWriteCheckpointGenesisSuccess(t *testing.T) {
ctx := setupMockContext(t)
stub := ctx.GetStub().(*mocks.ChaincodeStub)
stub.GetStateReturns(nil, nil) // No pre-existing state

entry, err := (&SmartContract{}).WriteCheckpoint(ctx, testName, testDataHash, "", testMetadata, testOffChainRef)
require.NoError(t, err)
require.NotNil(t, entry)

assert.Equal(t, testName, entry.Name)
assert.Equal(t, testDataHash, entry.DataHash)
assert.Equal(t, "", entry.PrevHash)
assert.Equal(t, 1, entry.Version)
assert.Equal(t, testMSPID, entry.Writer)
assert.False(t, entry.IsRollback)
// Timestamp is derived from GetTxTimestamp() — deterministic, not time.Now()
assert.Equal(t, testTimestampMs, entry.Timestamp)
}

func TestWriteCheckpointChainingSuccess(t *testing.T) {
ctx := setupMockContext(t)
stub := ctx.GetStub().(*mocks.ChaincodeStub)

// Simulate existing state with v1 pointing to testDataHash
state := []byte(`{"latestVersion":1,"latestHash":"` + testDataHash + `"}`)
stub.GetStateReturnsOnCall(0, state, nil)

entry, err := (&SmartContract{}).WriteCheckpoint(ctx, testName, testPrevHash, testDataHash, testMetadata, testOffChainRef)
require.NoError(t, err)
assert.Equal(t, testDataHash, entry.PrevHash)
assert.Equal(t, 2, entry.Version)
}

// ---------------------------------------------------------------------------
// WriteCheckpoint — validation
// ---------------------------------------------------------------------------

func TestWriteCheckpointValidation(t *testing.T) {
s := &SmartContract{}
ctx := setupMockContext(t)

cases := []struct {
label    string
name     string
hash     string
prev     string
meta     string
offchain string
wantErr  string
}{
{"invalid name",          "@bad!",    testDataHash, "",         testMetadata, testOffChainRef, "invalid checkpoint name"},
{"short hash",            testName,   "tooshort",   "",         testMetadata, testOffChainRef, "invalid data hash"},
{"invalid prev hash",     testName,   testDataHash, "badhash",  testMetadata, testOffChainRef, "invalid previous hash"},
{"invalid metadata JSON", testName,   testDataHash, "",         "not json",   testOffChainRef, "invalid metadataJson"},
{"path traversal ref",    testName,   testDataHash, "",         testMetadata, "../../etc/passwd", "path traversal"},
{"bad chars in ref",      testName,   testDataHash, "",         testMetadata, "bad ref!",      "invalid off-chain reference"},
}

for _, tc := range cases {
t.Run(tc.label, func(t *testing.T) {
_, err := s.WriteCheckpoint(ctx, tc.name, tc.hash, tc.prev, tc.meta, tc.offchain)
assert.ErrorContains(t, err, tc.wantErr)
})
}
}

func TestWriteCheckpointPrevHashMismatch(t *testing.T) {
ctx := setupMockContext(t)
stub := ctx.GetStub().(*mocks.ChaincodeStub)

// State records a different latest hash
wrongState := []byte(`{"latestVersion":1,"latestHash":"` + testPrevHash + `"}`)
stub.GetStateReturns(wrongState, nil)

_, err := (&SmartContract{}).WriteCheckpoint(ctx, testName, testDataHash, testDataHash, testMetadata, testOffChainRef)
assert.ErrorContains(t, err, "previous hash mismatch")
}

func TestWriteCheckpointGetStateError(t *testing.T) {
ctx := setupMockContext(t)
ctx.GetStub().(*mocks.ChaincodeStub).GetStateReturns(nil, fmt.Errorf("ledger unavailable"))

_, err := (&SmartContract{}).WriteCheckpoint(ctx, testName, testDataHash, "", testMetadata, testOffChainRef)
assert.ErrorContains(t, err, "failed to read checkpoint state")
}

// ---------------------------------------------------------------------------
// ReadCheckpoint
// ---------------------------------------------------------------------------

// TestReadCheckpointSpecificVersion verifies reading by explicit version string.
// NOTE: ReadCheckpoint now takes an explicit string param (not variadic).
func TestReadCheckpointSpecificVersion(t *testing.T) {
ctx := setupMockContext(t)
stub := ctx.GetStub().(*mocks.ChaincodeStub)

stored := CheckpointEntry{Name: testName, Version: 1}
raw, _ := json.Marshal(stored)
stub.GetStateReturns(raw, nil)

result, err := (&SmartContract{}).ReadCheckpoint(ctx, testName, "1")
require.NoError(t, err)
assert.Equal(t, &stored, result)
}

// TestReadCheckpointLatest passes "" to get the latest version.
// NOTE: pass "" (empty string), not omit — the param is no longer variadic.
func TestReadCheckpointLatest(t *testing.T) {
ctx := setupMockContext(t)
stub := ctx.GetStub().(*mocks.ChaincodeStub)

stateJSON := []byte(`{"latestVersion":1,"latestHash":"` + testDataHash + `"}`)
stub.GetStateReturnsOnCall(0, stateJSON, nil)

stored := CheckpointEntry{Name: testName, Version: 1}
raw, _ := json.Marshal(stored)
stub.GetStateReturnsOnCall(1, raw, nil)

result, err := (&SmartContract{}).ReadCheckpoint(ctx, testName, "")
require.NoError(t, err)
assert.Equal(t, &stored, result)
}

func TestReadCheckpointNotFound(t *testing.T) {
ctx := setupMockContext(t)
ctx.GetStub().(*mocks.ChaincodeStub).GetStateReturns(nil, nil)

_, err := (&SmartContract{}).ReadCheckpoint(ctx, testName, "1")
assert.ErrorContains(t, err, "does not exist")
}

// ---------------------------------------------------------------------------
// ReadCheckpointByHash
// ---------------------------------------------------------------------------

func TestReadCheckpointByHashSuccess(t *testing.T) {
ctx := setupMockContext(t)
stub := ctx.GetStub().(*mocks.ChaincodeStub)

primaryKey := []byte("Checkpoint\x00" + testName + "\x000000000001\x00")
stub.GetStateReturnsOnCall(0, primaryKey, nil)

stored := CheckpointEntry{Name: testName, DataHash: testDataHash}
raw, _ := json.Marshal(stored)
stub.GetStateReturnsOnCall(1, raw, nil)

result, err := (&SmartContract{}).ReadCheckpointByHash(ctx, testName, testDataHash)
require.NoError(t, err)
assert.Equal(t, &stored, result)
}

func TestReadCheckpointByHashNotFound(t *testing.T) {
ctx := setupMockContext(t)
ctx.GetStub().(*mocks.ChaincodeStub).GetStateReturns(nil, nil)

_, err := (&SmartContract{}).ReadCheckpointByHash(ctx, testName, testDataHash)
assert.ErrorContains(t, err, "not found")
}

// ---------------------------------------------------------------------------
// ReadCheckpointHistory
// ---------------------------------------------------------------------------

func TestReadCheckpointHistoryInvalidRange(t *testing.T) {
ctx := setupMockContext(t)
_, err := (&SmartContract{}).ReadCheckpointHistory(ctx, testName, 5, 2)
assert.ErrorContains(t, err, "invalid version range")
}

// ---------------------------------------------------------------------------
// RollbackCheckpoint
// ---------------------------------------------------------------------------

func TestRollbackCheckpointEmptyMessage(t *testing.T) {
ctx := setupMockContext(t)
_, err := (&SmartContract{}).RollbackCheckpoint(ctx, testName, testDataHash, "")
assert.ErrorContains(t, err, "rollback message cannot be empty")
}

func TestRollbackCheckpointTargetNotFound(t *testing.T) {
ctx := setupMockContext(t)
stub := ctx.GetStub().(*mocks.ChaincodeStub)
stub.GetStateReturnsOnCall(0, []byte(`{"latestVersion":1,"latestHash":"`+testDataHash+`"}`), nil)
stub.GetStateReturnsOnCall(1, nil, nil) // Target index not found

_, err := (&SmartContract{}).RollbackCheckpoint(ctx, testName, testPrevHash, testMessage)
assert.ErrorContains(t, err, "not found")
}

func TestRollbackCheckpointToCurrentIsNoop(t *testing.T) {
ctx := setupMockContext(t)
stub := ctx.GetStub().(*mocks.ChaincodeStub)

stateJSON := []byte(`{"latestVersion":1,"latestHash":"` + testDataHash + `"}`)
stub.GetStateReturnsOnCall(0, stateJSON, nil)

target := CheckpointEntry{DataHash: testDataHash, Version: 1}
targetJSON, _ := json.Marshal(target)
stub.GetStateReturnsOnCall(1, targetJSON, nil)

entry, err := (&SmartContract{}).RollbackCheckpoint(ctx, testName, testDataHash, testMessage)
require.NoError(t, err)
// No-op: returns the existing entry unchanged
assert.Equal(t, target.DataHash, entry.DataHash)
assert.Equal(t, target.Version, entry.Version)
}

// ---------------------------------------------------------------------------
// RBAC error message quality
// ---------------------------------------------------------------------------

// TestRBACErrorMessages verifies that RBAC failures produce specific errors
// that do not contain "<nil>" (which would indicate the old `!ok || err != nil`
// pattern where err was nil and got printed verbatim).
func TestRBACErrorMessages(t *testing.T) {
ctx := setupMockContext(t)
// Corrupt creator bytes so GetCreator cannot be parsed → deserialization error
ctx.GetStub().(*mocks.ChaincodeStub).GetCreatorReturns([]byte("not-proto"), nil)

_, err := (&SmartContract{}).WriteCheckpoint(ctx, testName, testDataHash, "", testMetadata, testOffChainRef)
if err != nil {
assert.NotContains(t, err.Error(), "<nil>",
"RBAC errors must not expose '<nil>' — check hasRole() error handling")
}
}

// ---------------------------------------------------------------------------
// Benchmarks
// ---------------------------------------------------------------------------

func BenchmarkWriteCheckpoint(b *testing.B) {
s := &SmartContract{}
ctx := setupMockContext(b)
ctx.GetStub().(*mocks.ChaincodeStub).GetStateReturns(nil, nil)

b.ResetTimer()
for i := 0; i < b.N; i++ {
_, _ = s.WriteCheckpoint(ctx, testName, testDataHash, "", testMetadata, testOffChainRef)
}
}

// Compile-time check that mocks.TransactionContext satisfies the interface.
var _ contractapi.TransactionContextInterface = (*mocks.TransactionContext)(nil)
