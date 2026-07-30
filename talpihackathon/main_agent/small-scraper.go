package main

import (
	"bufio"
	"bytes"
	"fmt"
	"io"
	"net/http"
	"strings"
	"time"
)

func main() {
	baseURL := "https://next.obudget.org/mcp"

	// 1. Handshake: Get an active session ID from the server
	resp, err := http.Get(baseURL)
	if err != nil {
		panic(err)
	}
	defer resp.Body.Close()

	sessionID := resp.Header.Get("Mcp-Session-Id")
	if sessionID == "" {
		body, _ := io.ReadAll(resp.Body)
		fmt.Printf("Status: %s\nBody: %s\n", resp.Status, body)
		panic("missing Mcp-Session-Id header in response")
	}
	fmt.Printf("Established session ID: %s\n", sessionID)

	// 2. Connect to SSE Stream using the retrieved Session ID
	client := &http.Client{}
	req, err := http.NewRequest("GET", baseURL, nil)
	if err != nil {
		panic(err)
	}
	req.Header.Set("Accept", "application/json, text/event-stream")
	req.Header.Set("Mcp-Session-Id", sessionID)

	sseResp, err := client.Do(req)
	if err != nil {
		panic(err)
	}
	defer sseResp.Body.Close()

	if sseResp.StatusCode != 200 {
		body, _ := io.ReadAll(sseResp.Body)
		panic(fmt.Sprintf("failed to connect to SSE: status %s, body: %s", sseResp.Status, body))
	}
	fmt.Println("Connected to SSE stream.")

	// Goroutine to read SSE events asynchronously
	go func() {
		reader := bufio.NewReader(sseResp.Body)
		for {
			line, err := reader.ReadString('\n')
			if err != nil {
				if err == io.EOF {
					fmt.Println("SSE Stream closed (EOF)")
					break
				}
				fmt.Printf("SSE read error: %v\n", err)
				break
			}
			line = strings.TrimSpace(line)
			if line != "" {
				fmt.Printf("SSE Event: %s\n", line)
			}
		}
	}()

	// Wait 1 second for the SSE connection to initialize
	time.Sleep(1 * time.Second)

	// 3. Send POST request (initialize method)
	initJSON := `{
		"jsonrpc": "2.0",
		"method": "initialize",
		"id": 1,
		"params": {
			"protocolVersion": "2024-11-05",
			"capabilities": {},
			"clientInfo": {
				"name": "small-go-client",
				"version": "1.0.0"
			}
		}
	}`

	sendPost := func(reqID int, method string, payload string) {
		postReq, err := http.NewRequest("POST", baseURL, bytes.NewBufferString(payload))
		if err != nil {
			panic(err)
		}
		postReq.Header.Set("Content-Type", "application/json")
		postReq.Header.Set("Accept", "application/json, text/event-stream")
		postReq.Header.Set("Mcp-Session-Id", sessionID)

		postResp, err := client.Do(postReq)
		if err != nil {
			panic(err)
		}
		defer postResp.Body.Close()

		postResponseBody, _ := io.ReadAll(postResp.Body)
		fmt.Printf("\n--- POST %s response ---\n", method)
		fmt.Printf("Status: %s\n", postResp.Status)
		fmt.Printf("Body:\n%s\n", postResponseBody)
	}

	sendPost(1, "initialize", initJSON)

	// Wait a bit before sending the next request
	time.Sleep(2 * time.Second)

	// 4. Send POST request (tools/call method for DatasetDBQuery)
	queryJSON := `{
		"jsonrpc": "2.0",
		"method": "tools/call",
		"id": 4,
		"params": {
			"name": "DatasetDBQuery",
			"arguments": {
				"dataset": "budget_items_data",
				"query": "SELECT code, title, year, amount_allocated, amount_revised, item_url FROM budget_items_data WHERE year = 2025 AND level = 1 ORDER BY amount_allocated DESC LIMIT 5"
			}
		}
	}`
	sendPost(4, "tools/call", queryJSON)

	// Keep the main thread alive to read the incoming SSE messages
	time.Sleep(5 * time.Second)
}
