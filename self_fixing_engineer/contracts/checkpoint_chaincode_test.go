// Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

// checkpoint_chaincode_test.go
// Comprehensive unit tests for checkpoint_chaincode.go
// This file includes tests for all chaincode functions, edge cases, validation, and error handling.
// It uses Fabric's mock stubs for isolation and testify for assertions.
// Coverage target: 90%+
// Run: go test -v -coverprofile=coverage.out ./...
// View coverage: go tool cover -html=coverage.out

package main_test

import (
	"encoding/json"
	"fmt"
	"os"
	"regexp"
	"testing"
	"time"

	"github.com/hyperledger/fabric-chaincode-go/shim"
	"github.com/hyperledger/fabric-contract-api-go/contractapi"
	"github.com/hyperledger/fabric-contract-api-go/mocks"
	"github.com/hyperledger/fabric-protos-go/msp"
	"github.com/hyperledger/fabric-protos-go/peer"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
	"google.golang.org/protobuf/proto"
	"github.com/avast/retry-go/v4"
)

// Import the chaincode
import . "main"  // Adjust if package name differs

// Test constants
const (
	testName         = "test_checkpoint"
	testDataHash     = "abcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890"  // Valid SHA256
	testPrevHash     = "1234567890abcdef1234567890abcdef1234567890abcdef1234567890"      // Valid SHA256
	testMetadata     = `{"key":"value"}`
	testOffChainRef  = "s3://bucket/key"
	testInvalidName  = "@invalid@"
	testInvalidHash  = "short"
	testMessage      = "Rollback test message"
	testMSPID        = "Org1MSP"
	testTxID         = "test_tx_id"
	testTimestamp    = 1627849200000  // Unix timestamp in ms
)

// Setup mock context with creator MSP
func setupMockContext(t *testing.T) *mocks.TransactionContext {
	ctx := new(mocks.TransactionContext)
	stub := new(mocks.ChaincodeStub)
	ctx.GetStubReturns(stub)

	// Mock creator (MSPID)
	serializedID := &msp.SerializedIdentity{Mspid: testMSPID}
	creatorBytes, err := proto.Marshal(serializedID)
	require.NoError(t, err)
	stub.GetCreatorReturns(creatorBytes, nil)

	// Mock TxID and Timestamp
	stub.GetTxIDReturns(testTxID)
	stub.GetTxTimestampReturns(&peer.Timestamp{Seconds: testTimestamp / 1000, Nanos: 0}, nil)

	return ctx
}

// TestInitLogger tests logger initialization with env var
func TestInitLogger(t *testing.T) {
	t.Run("Default INFO", func(t *testing.T) {
		os.Unsetenv("CORE_CHAINCODE_LOGGING_LEVEL")
		InitLogger()
		assert.Equal(t, shim.LogInfo, logger.GetLevel())
	})

	t.Run("DEBUG Level", func(t *testing.T) {
		os.Setenv("CORE_CHAINCODE_LOGGING_LEVEL", "DEBUG")
		InitLogger()
		assert.Equal(t, shim.LogDebug, logger.GetLevel())
		os.Unsetenv("CORE_CHAINCODE_LOGGING_LEVEL")
	})
}

// TestInitLedger tests ledger initialization
func TestInitLedger(t *testing.T) {
	s := &SmartContract{}
	ctx := setupMockContext(t)
	err := s.InitLedger(ctx)
	assert.NoError(t, err)
	// Add assertions if InitLedger writes state in future
}

// TestHealthCheck tests health check function
func TestHealthCheck(t *testing.T) {
	s := &SmartContract{}
	ctx := setupMockContext(t)
	result, err := s.HealthCheck(ctx)
	assert.NoError(t, err)
	assert.Equal(t, "Chaincode is healthy", result)
}

// TestHasRoleSuccess tests successful role check
func TestHasRoleSuccess(t *testing.T) {
	s := &SmartContract{}
	ctx := setupMockContext(t)
	ok, err := s.hasRole(ctx, testMSPID)
	assert.NoError(t, err)
	assert.True(t, ok)
}

// TestHasRoleFailure tests unauthorized role
func TestHasRoleFailure(t *testing.T) {
	s := &SmartContract{}
	ctx := setupMockContext(t)
	ok, err := s.hasRole(ctx, "UnauthorizedMSP")
	assert.NoError(t, err)
	assert.False(t, ok)
}

// TestWriteCheckpointSuccess tests successful write
func TestWriteCheckpointSuccess(t *testing.T) {
	s := &SmartContract{}
	ctx := setupMockContext(t)

	// Mock no existing state (genesis)
	ctx.GetStub().GetStateReturns(nil, nil)

	entry, err := s.WriteCheckpoint(ctx, testName, testDataHash, "", testMetadata, testOffChainRef)
	assert.NoError(t, err)
	assert.NotNil(t, entry)
	assert.Equal(t, testName, entry.Name)
	assert.Equal(t, testDataHash, entry.DataHash)
	assert.Equal(t, "", entry.PrevHash)
	assert.Equal(t, 1, entry.Version)
	assert.Equal(t, testMSPID, entry.Writer)  // From mock
	assert.False(t, entry.IsRollback)

	// Verify PutState calls (state, primary key, index)
	putCalls := ctx.GetStub().PutStateCalls()
	assert.Len(t, putCalls, 3)
	assert.Equal(t, []byte(`{"latestVersion":1,"latestHash":"`+testDataHash+`"}`), putCalls[2].Value)  // State update
}

// TestWriteCheckpointChainingSuccess tests chaining on subsequent write
func TestWriteCheckpointChainingSuccess(t *testing.T) {
	s := &SmartContract{}
	ctx := setupMockContext(t)

	// Mock existing state
	stateJSON := []byte(`{"latestVersion":1,"latestHash":"prev_hash"}`)
	ctx.GetStub().GetStateReturnsOnCall(0, stateJSON, nil)

	entry, err := s.WriteCheckpoint(ctx, testName, testDataHash, "prev_hash", testMetadata, testOffChainRef)
	assert.NoError(t, err)
	assert.Equal(t, "prev_hash", entry.PrevHash)
	assert.Equal(t, 2, entry.Version)
}

// TestWriteCheckpointInvalidInput tests validation errors
func TestWriteCheckpointInvalidInput(t *testing.T) {
	s := &SmartContract{}
	ctx := setupMockContext(t)

	tests := []struct {
		name        string
		dataHash    string
		prevHash    string
		metadata    string
		offChainRef string
		expErr      string
	}{
		{"invalid_name", testDataHash, testPrevHash, testMetadata, testOffChainRef, "invalid checkpoint name"},
		{testName, "short", testPrevHash, testMetadata, testOffChainRef, "invalid data hash"},
		{testName, testDataHash, "invalid", testMetadata, testOffChainRef, "invalid previous hash"},
		{testName, testDataHash, testPrevHash, "invalid json", testOffChainRef, "invalid metadataJson"},
		{testName, testDataHash, testPrevHash, testMetadata, "invalid/ref@", "invalid off-chain reference"},
	}

	for _, tt := range tests {
		t.Run(tt.expErr, func(t *testing.T) {
			_, err := s.WriteCheckpoint(ctx, tt.name, tt.dataHash, tt.prevHash, tt.metadata, tt.offChainRef)
			assert.ErrorContains(t, err, tt.expErr)
		})
	}
}

// TestWriteCheckpointPrevHashMismatch tests chaining failure
func TestWriteCheckpointPrevHashMismatch(t *testing.T) {
	s := &SmartContract{}
	ctx := setupMockContext(t)

	// Mock state with mismatch
	stateJSON := []byte(`{"latestVersion":1,"latestHash":"different_hash"}`)
	ctx.GetStub().GetStateReturns(stateJSON, nil)

	_, err := s.WriteCheckpoint(ctx, testName, testDataHash, testPrevHash, testMetadata, testOffChainRef)
	assert.ErrorContains(t, err, "checkpoint chaining error: previous hash mismatch")
}

// TestWriteCheckpointGetStateError tests GetState failure
func TestWriteCheckpointGetStateError(t *testing.T) {
	s := &SmartContract{}
	ctx := setupMockContext(t)
	ctx.GetStub().GetStateReturns(nil, fmt.Errorf("test error"))

	_, err := s.WriteCheckpoint(ctx, testName, testDataHash, testPrevHash, testMetadata, testOffChainRef)
	assert.ErrorContains(t, err, "failed to read checkpoint state")
}

// TestReadCheckpointByVersionSuccess tests reading specific version
func TestReadCheckpointByVersionSuccess(t *testing.T) {
	s := &SmartContract{}
	ctx := setupMockContext(t)

	// Mock entry
	entry := CheckpointEntry{Name: testName, Version: 1}
	entryJSON, _ := json.Marshal(entry)
	ctx.GetStub().GetStateReturns(entryJSON, nil)

	result, err := s.ReadCheckpoint(ctx, testName, "1")
	assert.NoError(t, err)
	assert.Equal(t, &entry, result)
}

// TestReadCheckpointLatestSuccess tests reading latest version
func TestReadCheckpointLatestSuccess(t *testing.T) {
	s := &SmartContract{}
	ctx := setupMockContext(t)

	// Mock state
	stateJSON := []byte(`{"latestVersion":1,"latestHash":"hash"}`)
	ctx.GetStub().GetStateReturnsOnCall(0, stateJSON, nil)

	// Mock entry
	entry := CheckpointEntry{Name: testName, Version: 1}
	entryJSON, _ := json.Marshal(entry)
	ctx.GetStub().GetStateReturnsOnCall(1, entryJSON, nil)

	result, err := s.ReadCheckpoint(ctx, testName)
	assert.NoError(t, err)
	assert.Equal(t, &entry, result)
}

// TestReadCheckpointNotFound tests not found error
func TestReadCheckpointNotFound(t *testing.T) {
	s := &SmartContract{}
	ctx := setupMockContext(t)
	ctx.GetStub().GetStateReturns(nil, nil)

	_, err := s.ReadCheckpoint(ctx, testName, "1")
	assert.ErrorContains(t, err, "does not exist")
}

// TestReadCheckpointByHashSuccess tests reading by hash
func TestReadCheckpointByHashSuccess(t *testing.T) {
	s := &SmartContract{}
	ctx := setupMockContext(t)

	// Mock index and entry
	primaryKey := []byte("Checkpoint~test_checkpoint~1")
	ctx.GetStub().GetStateReturnsOnCall(0, primaryKey, nil)  // Index
	entry := CheckpointEntry{Name: testName, DataHash: testDataHash}
	entryJSON, _ := json.Marshal(entry)
	ctx.GetStub().GetStateReturnsOnCall(1, entryJSON, nil)  // Entry

	result, err := s.ReadCheckpointByHash(ctx, testName, testDataHash)
	assert.NoError(t, err)
	assert.Equal(t, &entry, result)
}

// TestReadCheckpointByHashNotFound tests hash not found
func TestReadCheckpointByHashNotFound(t *testing.T) {
	s := &SmartContract{}
	ctx := setupMockContext(t)
	ctx.GetStub().GetStateReturns(nil, nil)  // Index not found

	_, err := s.ReadCheckpointByHash(ctx, testName, testDataHash)
	assert.ErrorContains(t, err, "not found")
}

// TestReadCheckpointHistorySuccess tests history read with range
func TestReadCheckpointHistorySuccess(t *testing.T) {
	s := &SmartContract{}
	ctx := setupMockContext(t)

	// Mock state
	stateJSON := []byte(`{"latestVersion":2,"latestHash":"hash2"}`)
	ctx.GetStub().GetStateReturnsOnCall(0, stateJSON, nil)

	// Mock entries
	entry1 := CheckpointEntry{Version: 1}
	entry1JSON, _ := json.Marshal(entry1)
	ctx.GetStub().GetStateReturnsOnCall(1, entry1JSON, nil)

	entry2 := CheckpointEntry{Version: 2}
	entry2JSON, _ := json.Marshal(entry2)
	ctx.GetStub().GetStateReturnsOnCall(2, entry2JSON, nil)

	history, err := s.ReadCheckpointHistory(ctx, testName, 1, 2)
	assert.NoError(t, err)
	assert.Len(t, history, 2)
	assert.Equal(t, entry1.Version, history[0].Version)
	assert.Equal(t, entry2.Version, history[1].Version)
}

// TestReadCheckpointHistoryInvalidRange tests invalid version range
func TestReadCheckpointHistoryInvalidRange(t *testing.T) {
	s := &SmartContract{}
	ctx := setupMockContext(t)

	_, err := s.ReadCheckpointHistory(ctx, testName, 2, 1)
	assert.ErrorContains(t, err, "invalid version range")
}

// TestRollbackCheckpointSuccess tests successful rollback
func TestRollbackCheckpointSuccess(t *testing.T) {
	s := &SmartContract{}
	ctx := setupMockContext(t)

	// Mock state
	stateJSON := []byte(`{"latestVersion":2,"latestHash":"current_hash"}`)
	ctx.GetStub().GetStateReturnsOnCall(0, stateJSON, nil)

	// Mock target entry by hash
	targetEntry := CheckpointEntry{DataHash: "target_hash", Version: 1, MetadataJson: `{"original":"data"}`}
	targetJSON, _ := json.Marshal(targetEntry)
	ctx.GetStub().GetStateReturnsOnCall(1, targetJSON, nil)  // Target entry

	entry, err := s.RollbackCheckpoint(ctx, testName, "target_hash", testMessage)
	assert.NoError(t, err)
	assert.True(t, entry.IsRollback)
	assert.Equal(t, 3, entry.Version)
	assert.Equal(t, "target_hash", entry.DataHash)
	assert.Contains(t, entry.MetadataJson, "rolledBackFromHash")

	// Verify PutState calls
	putCalls := ctx.GetStub().PutStateCalls()
	assert.Len(t, putCalls, 3)  // New entry, index, state update
}

// TestRollbackCheckpointToCurrent tests rollback to current (no-op)
func TestRollbackCheckpointToCurrent(t *testing.T) {
	s := &SmartContract{}
	ctx := setupMockContext(t)

	// Mock state with target as current
	stateJSON := []byte(`{"latestVersion":1,"latestHash":"target_hash"}`)
	ctx.GetStub().GetStateReturnsOnCall(0, stateJSON, nil)

	targetEntry := CheckpointEntry{DataHash: "target_hash", Version: 1}
	targetJSON, _ := json.Marshal(targetEntry)
	ctx.GetStub().GetStateReturnsOnCall(1, targetJSON, nil)

	entry, err := s.RollbackCheckpoint(ctx, testName, "target_hash", testMessage)
	assert.NoError(t, err)
	assert.Equal(t, targetEntry.DataHash, entry.DataHash)
	assert.Len(t, ctx.GetStub().PutStateCalls(), 0)  // No changes
}

// TestRollbackCheckpointInvalidMessage tests empty message validation
func TestRollbackCheckpointInvalidMessage(t *testing.T) {
	s := &SmartContract{}
	ctx := setupMockContext(t)

	_, err := s.RollbackCheckpoint(ctx, testName, testDataHash, "")
	assert.ErrorContains(t, err, "rollback message cannot be empty")
}

// TestRollbackCheckpointTargetNotFound tests target not found
func TestRollbackCheckpointTargetNotFound(t *testing.T) {
	s := &SmartContract{}
	ctx := setupMockContext(t)

	ctx.GetStub().GetStateReturnsOnCall(0, []byte(`{"latestVersion":1,"latestHash":"hash"}`), nil)
	ctx.GetStub().GetStateReturnsOnCall(1, nil, nil)  // Target not found

	_, err := s.RollbackCheckpoint(ctx, testName, "nonexistent", testMessage)
	assert.ErrorContains(t, err, "target checkpoint with hash nonexistent not found")
}

// BenchmarkWriteCheckpoint for performance
func BenchmarkWriteCheckpoint(b *testing.B) {
	s := &SmartContract{}
	ctx := setupMockContext(b)

	for i := 0; i < b.N; i++ {
		_, err := s.WriteCheckpoint(ctx, testName, testDataHash, testPrevHash, testMetadata, testOffChainRef)
		if err != nil {
			b.Fatal(err)
		}
	}
}

// BenchmarkReadCheckpointHistory for performance
func BenchmarkReadCheckpointHistory(b *testing.B) {
	s := &SmartContract{}
	ctx := setupMockContext(b)

	// Setup mock history (simulate 100 entries)
	for i := 1; i <= 100; i++ {
		entry := CheckpointEntry{Version: i}
		entryJSON, _ := json.Marshal(entry)
		ctx.GetStub().GetStateReturnsOnCall(i-1, entryJSON, nil)
	}

	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		_, err := s.ReadCheckpointHistory(ctx, testName, 1, 100)
		if err != nil {
			b.Fatal(err)
		}
	}
}

// Run with: go test -v -bench=.
// Coverage: go test -cover
// Security scan: gosec ./...