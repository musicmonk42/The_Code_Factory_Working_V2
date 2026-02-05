# Simple Calculator API

A RESTful API for basic arithmetic operations built with Python and FastAPI.

## Requirements

Build a calculator API that provides the following operations:
- Addition
- Subtraction
- Multiplication
- Division

## API Endpoints

### POST /calculate
Calculate the result of an arithmetic operation.

**Request Body:**
```json
{
  "operation": "add",
  "a": 10,
  "b": 5
}
```

**Supported operations:**
- `add` - Addition
- `subtract` - Subtraction
- `multiply` - Multiplication
- `divide` - Division

**Response:**
```json
{
  "result": 15,
  "operation": "add",
  "operands": {
    "a": 10,
    "b": 5
  }
}
```

### GET /health
Health check endpoint.

**Response:**
```json
{
  "status": "healthy",
  "service": "calculator-api"
}
```

## Technical Requirements

- Python 3.8+
- FastAPI framework
- Input validation
- Error handling for division by zero
- Unit tests with pytest
- API documentation with OpenAPI/Swagger

## Error Handling

The API should handle the following error cases:
- Invalid operation type
- Missing operands
- Division by zero
- Non-numeric operands

## Testing

Include unit tests for:
- All arithmetic operations
- Error handling
- Input validation
- Health check endpoint
