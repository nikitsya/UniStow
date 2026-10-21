package com.example.paybox

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.dp
import com.example.paybox.ui.theme.PayBoxTheme

class MainActivity : ComponentActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        setContent {
            var showDetails by remember { mutableStateOf(false) }

            PayBoxTheme {
                Scaffold(
                    modifier = Modifier.fillMaxSize(),
                    containerColor = Color(0xFF183B35)
                ) { innerPadding ->
                    if (showDetails) {
                        Text(
                            text = "Locker Details",
                            modifier = Modifier.padding(innerPadding)
                        )
                    } else {
                        LockerList(
                            modifier = Modifier.padding(innerPadding),
                            onLockerClick = { showDetails = true }
                        )
                    }
                }
            }
        }
    }

    @Composable
    fun LockerList(
        modifier: Modifier = Modifier,
        onLockerClick: () -> Unit = {}
    ) {
        val unavailableLockers = listOf(3, 5, 6, 9)

        LazyColumn(modifier = modifier) {
            items(9) { index ->
                val lockerNumber = index + 1
                val isAvailable = lockerNumber !in unavailableLockers

                Card(
                    onClick = onLockerClick,
                    enabled = isAvailable,
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(8.dp),
                    colors = CardDefaults.cardColors(
                        containerColor = Color.White,
                        contentColor = Color(0xFF183B35),
                        disabledContainerColor = Color(0xFFE0E0E0),
                        disabledContentColor = Color(0xFF595959)
                    )
                ) {
                    Column(modifier = Modifier.padding(16.dp)) {
                        Text(
                            text = "Locker $lockerNumber",
                            style = MaterialTheme.typography.titleMedium
                        )
                        Text(
                            text = if (index < 6) "Size: Small" else "Size: Large",
                            style = MaterialTheme.typography.bodyMedium
                        )
                        Text(
                            text = if (isAvailable) "Status: Available" else "Status: Unavailable",
                            style = MaterialTheme.typography.bodyMedium
                        )
                        Text(
                            text = "€1 per day",
                            style = MaterialTheme.typography.bodyMedium
                        )
                    }
                }
            }
        }
    }

    @Preview(showBackground = true)
    @Composable
    fun LockerListPreview() {
        PayBoxTheme {
            LockerList()
        }
    }
}
