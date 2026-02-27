// Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

// checkpoint_chaincode_test.go — unit tests for checkpoint_chaincode.go.
//
// Package main is used (white-box testing) so unexported identifiers such as
// levelInfo, levelDebug, and logger are accessible.
//
// Usage:
//
//	go test -v -count=1 -coverprofile=coverage.out ./...
//	go tool cover -html=coverage.out
//	govulncheck ./...
package main

import (
	"encoding/json"
	"crypto/x509"
	"os"
	"strings"
	"testing"

	"github.com/golang/protobuf/proto"
	"github.com/golang/protobuf/ptypes/timestamp"
	"github.com/hyperledger/fabric-chaincode-go/pkg/cid"
	"github.com/hyperledger/fabric-chaincode-go/shim"
	"github.com/hyperledger/fabric-contract-api-go/contractapi"
	"github.com/hyperledger/fabric-protos-go/ledger/queryresult"
	"github.com/hyperledger/fabric-protos-go/msp"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

// ---------------------------------------------------------------------------
// Test constants
// ---------------------------------------------------------------------------

const (
	tcName       = "test_checkpoint"
	tcDataHash   = "abcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890"
	tcPrevHash   = "1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef"
	tcMetadata   = `{"env":"test"}`
	tcOffChain   = "bucket/object-key"
	tcMessage    = "rollback reason"
	tcAdminMSP   = "admin"
	tcReaderMSP  = "reader"
	tcAuditorMSP = "auditor"
	tcEpochSec   = int64(1700000000)
)

// ---------------------------------------------------------------------------
// testStub — minimal in-memory ChaincodeStubInterface
// ---------------------------------------------------------------------------

// testStub embeds shim.ChaincodeStubInterface so it satisfies the type system.
// Any method not overridden will panic if called — this is intentional so tests
// fail loudly when unexpected stub methods are invoked.
type testStub struct {
	shim.ChaincodeStubInterface
	state    map[string][]byte
	txID     string
	creator  []byte
	epochSec int64
}

func newTestStub(mspID string) *testStub {
	sid := &msp.SerializedIdentity{Mspid: mspID}
	b, _ := proto.Marshal(sid)
	return &testStub{
		state:    make(map[string][]byte),
		txID:     "txid-" + mspID,
		creator:  b,
		epochSec: tcEpochSec,
	}
}

func (s *testStub) GetState(key string) ([]byte, error)   { return s.state[key], nil }
func (s *testStub) PutState(key string, v []byte) error   { s.state[key] = v; return nil }
func (s *testStub) DelState(key string) error             { delete(s.state, key); return nil }
func (s *testStub) GetTxID() string                       { return s.txID }
func (s *testStub) GetCreator() ([]byte, error)           { return s.creator, nil }
func (s *testStub) GetTxTimestamp() (*timestamp.Timestamp, error) {
	return &timestamp.Timestamp{Seconds: s.epochSec, Nanos: 0}, nil
}

func (s *testStub) CreateCompositeKey(objectType string, attrs []string) (string, error) {
	parts := append([]string{objectType}, attrs...)
	return "\x00" + strings.Join(parts, "\x00") + "\x00", nil
}

func (s *testStub) SplitCompositeKey(key string) (string, []string, error) {
	trimmed := strings.Trim(key, "\x00")
	parts := strings.Split(trimmed, "\x00")
	if len(parts) == 0 {
		return "", nil, nil
	}
	return parts[0], parts[1:], nil
}

// GetStateByRange returns an in-memory range iterator over the stub's state.
func (s *testStub) GetStateByRange(start, end string) (shim.StateQueryIteratorInterface, error) {
	var items []*queryresult.KV
	for k, v := range s.state {
		if k >= start && k < end {
			items = append(items, &queryresult.KV{Key: k, Value: v})
		}
	}
	return &memIter{items: items}, nil
}

// ---------------------------------------------------------------------------
// memIter — in-memory StateQueryIteratorInterface
// ---------------------------------------------------------------------------

type memIter struct {
	items []*queryresult.KV
	pos   int
}

func (it *memIter) HasNext() bool                       { return it.pos < len(it.items) }
func (it *memIter) Next() (*queryresult.KV, error)      { qr := it.items[it.pos]; it.pos++; return qr, nil }
func (it *memIter) Close() error                        { return nil }

// ---------------------------------------------------------------------------
// mockClientIdentity — minimal cid.ClientIdentity
// ---------------------------------------------------------------------------

type mockClientIdentity struct{ mspID string }

func (m *mockClientIdentity) GetID() (string, error)           { return m.mspID, nil }
func (m *mockClientIdentity) GetMSPID() (string, error)        { return m.mspID, nil }
func (m *mockClientIdentity) GetAttributeValue(_ string) (string, bool, error) {
	return "", false, nil
}
func (m *mockClientIdentity) AssertAttributeValue(_, _ string) error { return nil }
func (m *mockClientIdentity) GetX509Certificate() (*x509.Certificate, error) {
	return nil, nil
}

// ---------------------------------------------------------------------------
// testCtx — minimal contractapi.TransactionContextInterface
// ---------------------------------------------------------------------------

type testCtx struct {
	stub  *testStub
	mspID string
}

func newCtx(mspID string) *testCtx { return &testCtx{stub: newTestStub(mspID), mspID: mspID} }

func (c *testCtx) GetStub() shim.ChaincodeStubInterface     { return c.stub }
func (c *testCtx) GetClientIdentity() cid.ClientIdentity    { return &mockClientIdentity{c.mspID} }

// shareState copies the ledger state map from src into dst so both contexts
// operate on the same in-memory world state.
func shareState(dst, src *testCtx) { dst.stub.state = src.stub.state }

// Compile-time assertions that our mocks satisfy the required interfaces.
var _ shim.StateQueryIteratorInterface = (*memIter)(nil)
var _ cid.ClientIdentity = (*mockClientIdentity)(nil)
var _ contractapi.TransactionContextInterface = (*testCtx)(nil)

// ---------------------------------------------------------------------------
// InitLogger
// ---------------------------------------------------------------------------

func TestInitLogger_Levels(t *testing.T) {
	tests := []struct {
		envVal string
		want   int
	}{
		{"INFO", levelInfo},
		{"DEBUG", levelDebug},
		{"WARNING", levelWarning},
		{"ERROR", levelError},
		{"", levelInfo}, // default
	}
	for _, tc := range tests {
		t.Run(tc.envVal, func(t *testing.T) {
			t.Setenv("CORE_CHAINCODE_LOGGING_SHIM", tc.envVal)
			InitLogger()
			assert.Equal(t, tc.want, logger.GetLevel())
		})
	}
	os.Unsetenv("CORE_CHAINCODE_LOGGING_SHIM")
	InitLogger()
}

// ---------------------------------------------------------------------------
// InitLedger / HealthCheck
// ---------------------------------------------------------------------------

func TestInitLedger(t *testing.T) {
	assert.NoError(t, (&SmartContract{}).InitLedger(newCtx(tcAdminMSP)))
}

func TestHealthCheck(t *testing.T) {
	result, err := (&SmartContract{}).HealthCheck(newCtx(tcAdminMSP))
	require.NoError(t, err)
	assert.Equal(t, "Chaincode is healthy", result)
}

// ---------------------------------------------------------------------------
// WriteCheckpoint — input validation
// ---------------------------------------------------------------------------

func TestWriteCheckpoint_InvalidName(t *testing.T) {
	_, err := (&SmartContract{}).WriteCheckpoint(newCtx(tcAdminMSP), "@bad!", tcDataHash, "", tcMetadata, tcOffChain)
	assert.ErrorContains(t, err, "invalid checkpoint name")
}

func TestWriteCheckpoint_InvalidDataHash(t *testing.T) {
	_, err := (&SmartContract{}).WriteCheckpoint(newCtx(tcAdminMSP), tcName, "short", "", tcMetadata, tcOffChain)
	assert.ErrorContains(t, err, "invalid data hash")
}

func TestWriteCheckpoint_InvalidPrevHash(t *testing.T) {
	_, err := (&SmartContract{}).WriteCheckpoint(newCtx(tcAdminMSP), tcName, tcDataHash, "badhash", tcMetadata, tcOffChain)
	assert.ErrorContains(t, err, "invalid previous hash")
}

func TestWriteCheckpoint_InvalidMetadata(t *testing.T) {
	_, err := (&SmartContract{}).WriteCheckpoint(newCtx(tcAdminMSP), tcName, tcDataHash, "", "not-json", tcOffChain)
	assert.ErrorContains(t, err, "invalid metadataJson")
}

func TestWriteCheckpoint_PathTraversalRef(t *testing.T) {
	_, err := (&SmartContract{}).WriteCheckpoint(newCtx(tcAdminMSP), tcName, tcDataHash, "", tcMetadata, "../../etc/passwd")
	assert.ErrorContains(t, err, "path traversal")
}

func TestWriteCheckpoint_BadCharsRef(t *testing.T) {
	_, err := (&SmartContract{}).WriteCheckpoint(newCtx(tcAdminMSP), tcName, tcDataHash, "", tcMetadata, "bad ref!")
	assert.ErrorContains(t, err, "invalid off-chain reference")
}

func TestWriteCheckpoint_TooLongRef(t *testing.T) {
	_, err := (&SmartContract{}).WriteCheckpoint(newCtx(tcAdminMSP), tcName, tcDataHash, "", tcMetadata, strings.Repeat("a", MaxOffChainRefLen+1))
	assert.ErrorContains(t, err, "exceeds maximum length")
}

// ---------------------------------------------------------------------------
// WriteCheckpoint — genesis & chaining
// ---------------------------------------------------------------------------

func TestWriteCheckpoint_GenesisSuccess(t *testing.T) {
	ctx := newCtx(tcAdminMSP)
	entry, err := (&SmartContract{}).WriteCheckpoint(ctx, tcName, tcDataHash, "", tcMetadata, tcOffChain)
	require.NoError(t, err)
	require.NotNil(t, entry)

	assert.Equal(t, tcName, entry.Name)
	assert.Equal(t, tcDataHash, entry.DataHash)
	assert.Equal(t, "", entry.PrevHash)
	assert.Equal(t, 1, entry.Version)
	assert.Equal(t, tcAdminMSP, entry.Writer)
	assert.False(t, entry.IsRollback)
	// Timestamp derived from stub — deterministic, not time.Now()
	assert.Equal(t, tcEpochSec*1000, entry.Timestamp)
}

func TestWriteCheckpoint_FirstEntryNonEmptyPrevHashFails(t *testing.T) {
	_, err := (&SmartContract{}).WriteCheckpoint(newCtx(tcAdminMSP), tcName, tcDataHash, tcPrevHash, tcMetadata, "")
	assert.ErrorContains(t, err, "first checkpoint must have an empty previous hash")
}

func TestWriteCheckpoint_ChainingSuccess(t *testing.T) {
	s := &SmartContract{}
	ctx := newCtx(tcAdminMSP)
	e1, err := s.WriteCheckpoint(ctx, tcName, tcDataHash, "", tcMetadata, "")
	require.NoError(t, err)
	assert.Equal(t, 1, e1.Version)

	e2, err := s.WriteCheckpoint(ctx, tcName, tcPrevHash, tcDataHash, tcMetadata, "")
	require.NoError(t, err)
	assert.Equal(t, 2, e2.Version)
	assert.Equal(t, tcDataHash, e2.PrevHash)
}

func TestWriteCheckpoint_PrevHashMismatch(t *testing.T) {
	s := &SmartContract{}
	ctx := newCtx(tcAdminMSP)
	_, err := s.WriteCheckpoint(ctx, tcName, tcDataHash, "", tcMetadata, "")
	require.NoError(t, err)
	_, err = s.WriteCheckpoint(ctx, tcName, tcPrevHash, strings.Repeat("f", SHA256HashLen), tcMetadata, "")
	assert.ErrorContains(t, err, "previous hash mismatch")
}

// ---------------------------------------------------------------------------
// Version key zero-padding
// ---------------------------------------------------------------------------

func TestWriteCheckpoint_VersionKeyZeroPadded(t *testing.T) {
	ctx := newCtx(tcAdminMSP)
	_, err := (&SmartContract{}).WriteCheckpoint(ctx, tcName, tcDataHash, "", tcMetadata, "")
	require.NoError(t, err)

	key, _ := ctx.stub.CreateCompositeKey("Checkpoint", []string{tcName, "0000000001"})
	raw := ctx.stub.state[key]
	require.NotNil(t, raw, "zero-padded primary key must exist in state")

	var stored CheckpointEntry
	require.NoError(t, json.Unmarshal(raw, &stored))
	assert.Equal(t, 1, stored.Version)
}

// ---------------------------------------------------------------------------
// ReadCheckpoint
// ---------------------------------------------------------------------------

func TestReadCheckpoint_Latest(t *testing.T) {
	s := &SmartContract{}
	wCtx := newCtx(tcAdminMSP)
	_, err := s.WriteCheckpoint(wCtx, tcName, tcDataHash, "", tcMetadata, "")
	require.NoError(t, err)

	rCtx := newCtx(tcReaderMSP)
	shareState(rCtx, wCtx)

	entry, err := s.ReadCheckpoint(rCtx, tcName, "")
	require.NoError(t, err)
	assert.Equal(t, tcDataHash, entry.DataHash)
	assert.Equal(t, 1, entry.Version)
}

func TestReadCheckpoint_SpecificVersion(t *testing.T) {
	s := &SmartContract{}
	wCtx := newCtx(tcAdminMSP)
	_, err := s.WriteCheckpoint(wCtx, tcName, tcDataHash, "", tcMetadata, "")
	require.NoError(t, err)

	rCtx := newCtx(tcReaderMSP)
	shareState(rCtx, wCtx)

	entry, err := s.ReadCheckpoint(rCtx, tcName, "1")
	require.NoError(t, err)
	assert.Equal(t, 1, entry.Version)
}

func TestReadCheckpoint_ZeroPaddedInput(t *testing.T) {
	s := &SmartContract{}
	wCtx := newCtx(tcAdminMSP)
	_, err := s.WriteCheckpoint(wCtx, tcName, tcDataHash, "", tcMetadata, "")
	require.NoError(t, err)

	rCtx := newCtx(tcReaderMSP)
	shareState(rCtx, wCtx)

	entry, err := s.ReadCheckpoint(rCtx, tcName, "0000000001")
	require.NoError(t, err)
	assert.Equal(t, 1, entry.Version)
}

func TestReadCheckpoint_NotFound(t *testing.T) {
	_, err := (&SmartContract{}).ReadCheckpoint(newCtx(tcReaderMSP), tcName, "99")
	assert.ErrorContains(t, err, "does not exist")
}

func TestReadCheckpoint_InvalidVersionFormat(t *testing.T) {
	_, err := (&SmartContract{}).ReadCheckpoint(newCtx(tcReaderMSP), tcName, "notanumber")
	assert.ErrorContains(t, err, "invalid version format")
}

// ---------------------------------------------------------------------------
// ReadCheckpointByHash
// ---------------------------------------------------------------------------

func TestReadCheckpointByHash_Success(t *testing.T) {
	s := &SmartContract{}
	wCtx := newCtx(tcAdminMSP)
	_, err := s.WriteCheckpoint(wCtx, tcName, tcDataHash, "", tcMetadata, "")
	require.NoError(t, err)

	rCtx := newCtx(tcReaderMSP)
	shareState(rCtx, wCtx)

	entry, err := s.ReadCheckpointByHash(rCtx, tcName, tcDataHash)
	require.NoError(t, err)
	assert.Equal(t, tcDataHash, entry.DataHash)
}

func TestReadCheckpointByHash_NotFound(t *testing.T) {
	_, err := (&SmartContract{}).ReadCheckpointByHash(newCtx(tcReaderMSP), tcName, tcDataHash)
	assert.ErrorContains(t, err, "not found")
}

// ---------------------------------------------------------------------------
// ReadCheckpointHistory
// ---------------------------------------------------------------------------

func TestReadCheckpointHistory_InvalidRange(t *testing.T) {
	_, err := (&SmartContract{}).ReadCheckpointHistory(newCtx(tcAuditorMSP), tcName, 5, 2)
	assert.ErrorContains(t, err, "invalid version range")
}

func TestReadCheckpointHistory_EmptyResult(t *testing.T) {
	entries, err := (&SmartContract{}).ReadCheckpointHistory(newCtx(tcAuditorMSP), tcName, 1, 10)
	require.NoError(t, err)
	assert.Empty(t, entries)
}

func TestReadCheckpointHistory_ReturnsTwoEntries(t *testing.T) {
	s := &SmartContract{}
	wCtx := newCtx(tcAdminMSP)
	_, err := s.WriteCheckpoint(wCtx, tcName, tcDataHash, "", tcMetadata, "")
	require.NoError(t, err)
	_, err = s.WriteCheckpoint(wCtx, tcName, tcPrevHash, tcDataHash, tcMetadata, "")
	require.NoError(t, err)

	aCtx := newCtx(tcAuditorMSP)
	shareState(aCtx, wCtx)

	entries, err := s.ReadCheckpointHistory(aCtx, tcName, 1, 2)
	require.NoError(t, err)
	assert.Len(t, entries, 2)
}

// ---------------------------------------------------------------------------
// RollbackCheckpoint
// ---------------------------------------------------------------------------

func TestRollbackCheckpoint_EmptyMessage(t *testing.T) {
	_, err := (&SmartContract{}).RollbackCheckpoint(newCtx(tcAdminMSP), tcName, tcDataHash, "")
	assert.ErrorContains(t, err, "rollback message cannot be empty")
}

func TestRollbackCheckpoint_TargetNotFound(t *testing.T) {
	s := &SmartContract{}
	ctx := newCtx(tcAdminMSP)
	_, err := s.WriteCheckpoint(ctx, tcName, tcDataHash, "", tcMetadata, "")
	require.NoError(t, err)
	_, err = s.RollbackCheckpoint(ctx, tcName, tcPrevHash, tcMessage)
	assert.ErrorContains(t, err, "not found")
}

func TestRollbackCheckpoint_ToCurrentIsNoop(t *testing.T) {
	s := &SmartContract{}
	ctx := newCtx(tcAdminMSP)
	genesis, err := s.WriteCheckpoint(ctx, tcName, tcDataHash, "", tcMetadata, "")
	require.NoError(t, err)

	result, err := s.RollbackCheckpoint(ctx, tcName, tcDataHash, tcMessage)
	require.NoError(t, err)
	assert.Equal(t, genesis.Version, result.Version)
	assert.Equal(t, genesis.DataHash, result.DataHash)
}

func TestRollbackCheckpoint_Success(t *testing.T) {
	s := &SmartContract{}
	ctx := newCtx(tcAdminMSP)
	_, err := s.WriteCheckpoint(ctx, tcName, tcDataHash, "", tcMetadata, "")
	require.NoError(t, err)
	_, err = s.WriteCheckpoint(ctx, tcName, tcPrevHash, tcDataHash, tcMetadata, "")
	require.NoError(t, err)

	rollback, err := s.RollbackCheckpoint(ctx, tcName, tcDataHash, tcMessage)
	require.NoError(t, err)
	assert.True(t, rollback.IsRollback)
	assert.Equal(t, tcDataHash, rollback.DataHash)
	assert.Equal(t, 3, rollback.Version)
	assert.Contains(t, rollback.MetadataJson, "rolledBackFromHash")
	assert.Contains(t, rollback.MetadataJson, tcMessage)
	// Timestamp must be deterministic
	assert.Equal(t, tcEpochSec*1000, rollback.Timestamp)
}

// ---------------------------------------------------------------------------
// RBAC — error messages must be specific, must not contain "<nil>"
// ---------------------------------------------------------------------------

func TestRBAC_WriteCheckpoint_WrongRole(t *testing.T) {
	_, err := (&SmartContract{}).WriteCheckpoint(newCtx(tcReaderMSP), tcName, tcDataHash, "", tcMetadata, "")
	require.Error(t, err)
	assert.Contains(t, err.Error(), "unauthorized")
	assert.NotContains(t, err.Error(), "<nil>")
}

func TestRBAC_ReadCheckpoint_WrongRole(t *testing.T) {
	_, err := (&SmartContract{}).ReadCheckpoint(newCtx(tcAdminMSP), tcName, "")
	require.Error(t, err)
	assert.Contains(t, err.Error(), "unauthorized")
	assert.NotContains(t, err.Error(), "<nil>")
}

func TestRBAC_ReadCheckpointHistory_WrongRole(t *testing.T) {
	_, err := (&SmartContract{}).ReadCheckpointHistory(newCtx(tcReaderMSP), tcName, 1, 10)
	require.Error(t, err)
	assert.Contains(t, err.Error(), "unauthorized")
	assert.NotContains(t, err.Error(), "<nil>")
}

func TestRBAC_RollbackCheckpoint_WrongRole(t *testing.T) {
	_, err := (&SmartContract{}).RollbackCheckpoint(newCtx(tcReaderMSP), tcName, tcDataHash, tcMessage)
	require.Error(t, err)
	assert.Contains(t, err.Error(), "unauthorized")
	assert.NotContains(t, err.Error(), "<nil>")
}

// ---------------------------------------------------------------------------
// Secondary index — rollback must not overwrite original entry's pointer
// ---------------------------------------------------------------------------

func TestRollback_SecondaryIndexNotOverwritten(t *testing.T) {
	s := &SmartContract{}
	ctx := newCtx(tcAdminMSP)
	_, err := s.WriteCheckpoint(ctx, tcName, tcDataHash, "", tcMetadata, "")
	require.NoError(t, err)
	_, err = s.WriteCheckpoint(ctx, tcName, tcPrevHash, tcDataHash, tcMetadata, "")
	require.NoError(t, err)

	indexKey, _ := ctx.stub.CreateCompositeKey("DataHashIndex", []string{tcName, tcDataHash})
	originalPtr := string(ctx.stub.state[indexKey])
	require.NotEmpty(t, originalPtr)

	_, err = s.RollbackCheckpoint(ctx, tcName, tcDataHash, tcMessage)
	require.NoError(t, err)

	// The index must still point to the original entry, not the rollback entry
	assert.Equal(t, originalPtr, string(ctx.stub.state[indexKey]))
}
