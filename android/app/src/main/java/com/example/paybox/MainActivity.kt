package com.example.paybox

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TextField
import androidx.compose.material3.TextFieldDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.dp
import com.example.paybox.ui.theme.PayBoxTheme

class MainActivity : ComponentActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        setContent {
            var selectedLocker by remember { mutableStateOf<Int?>(null) }

            PayBoxTheme {
                Scaffold(
                    modifier = Modifier.fillMaxSize(),
                    containerColor = Color(0xFF183B35)
                ) { innerPadding ->
                    if (selectedLocker != null) {
                        var daysInput by remember { mutableStateOf("") }
                        val days = daysInput.toIntOrNull()
                        val pricePerDay = if ((selectedLocker ?: 0) <= 6) 1.0 else 1.5
                        val totalPrice = if (days != null && days in 1..7) days * pricePerDay else 0
                        val isError = daysInput.isNotEmpty() && (days == null || days !in 1..7)

                        Column(
                            modifier = Modifier
                                .padding(innerPadding)
                                .padding(24.dp)
                                .fillMaxWidth(),
                            verticalArrangement = Arrangement.spacedBy(16.dp)
                        ) {
                            Text(
                                text = "Locker $selectedLocker",
                                style = MaterialTheme.typography.headlineMedium,
                                color = Color.White
                            )
                            TextField(
                                value = daysInput,
                                onValueChange = { daysInput = it },
                                modifier = Modifier.fillMaxWidth(),
                                label = { Text("Number of days") },
                                singleLine = true,
                                keyboardOptions = KeyboardOptions(
                                    keyboardType = KeyboardType.Number
                                ),
                                isError = isError,
                                shape = RoundedCornerShape(12.dp),
                                colors = TextFieldDefaults.colors(
                                    focusedContainerColor = Color(0xFFF5F2EC),
                                    unfocusedContainerColor = Color(0xFFF5F2EC),
                                    errorContainerColor = Color(0xFFFFEDEA),

                                    focusedTextColor = Color(0xFF183B35),
                                    unfocusedTextColor = Color(0xFF183B35),
                                    errorTextColor = Color(0xFF183B35),

                                    focusedLabelColor = Color(0xFF183B35),
                                    unfocusedLabelColor = Color(0xFF52645F),
                                    errorLabelColor = Color(0xFF9C2929),

                                    cursorColor = Color(0xFF183B35),
                                    errorCursorColor = Color(0xFF9C2929),

                                    focusedIndicatorColor = Color.Transparent,
                                    unfocusedIndicatorColor = Color.Transparent,
                                    errorIndicatorColor = Color(0xFF9C2929),

                                    focusedSupportingTextColor = Color(0xFFDCE6E2),
                                    unfocusedSupportingTextColor = Color(0xFFDCE6E2),
                                    errorSupportingTextColor = Color(0xFFFFB4AB)
                                ),
                                supportingText = {
                                    Text(
                                        text = if (isError) {
                                            "Enter between 1 and 7 days"
                                        } else {
                                            "Rent for up to 7 days"
                                        }
                                    )
                                }
                            )
                            Text(
                                text = "Price per day: €%.2f".format(pricePerDay),
                                color = Color.White
                            )
                            Text(
                                text = "Total price: €%.2f".format(totalPrice),
                                color = Color.White
                            )
                            Button(
                                onClick = {
                                    // TODO: Implement payment logic
                                },
                                enabled = days != null && days in 1..7,
                                modifier = Modifier.fillMaxWidth(),
                                colors = ButtonDefaults.buttonColors(
                                    containerColor = Color(0xFFE8D5B5),
                                    contentColor = Color(0xFF183B35),
                                    disabledContainerColor = Color(0xFF385650),
                                    disabledContentColor = Color(0xFFB8C7C2)
                                )
                            ) {
                                Text("Confirm rental")
                            }
                            Button(
                                onClick = { selectedLocker = null },
                                modifier = Modifier.fillMaxWidth(),
                                colors = ButtonDefaults.buttonColors(
                                    contentColor = Color.White
                                )
                            ) {
                                Text("Back")
                            }
                        }
                    } else {
                        LockerList(
                            modifier = Modifier.padding(innerPadding),
                            onLockerClick = { number ->
                                selectedLocker = number
                            }
                        )
                    }
                }
            }
        }
    }

    @Composable
    fun LockerList(
        modifier: Modifier = Modifier,
        onLockerClick: (Int) -> Unit = {}
    ) {
        val unavailableLockers = listOf(3, 5, 6, 9)

        LazyColumn(modifier = modifier) {
            item {
                Column(
                    modifier = Modifier.padding(
                        horizontal = 16.dp,
                        vertical = 24.dp
                    ),
                    verticalArrangement = Arrangement.spacedBy(8.dp)
                ) {
                    Text(
                        text = "PayBox",
                        style = MaterialTheme.typography.headlineLarge,
                        color = Color.White
                    )
                    Text(
                        text = "Select a locker to rent",
                        style = MaterialTheme.typography.titleMedium,
                        color = Color.White
                    )
                    Text(
                        text = "Small €1 · Large €1.50 per day",
                        style = MaterialTheme.typography.bodyMedium,
                        color = Color(0xFFDCE6E2)
                    )
                }
            }

            items(9) { index ->
                val lockerNumber = index + 1
                val isAvailable = lockerNumber !in unavailableLockers

                Card(
                    onClick = { onLockerClick(lockerNumber) },
                    enabled = isAvailable,
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(8.dp),
                    shape = RoundedCornerShape(16.dp),
                    colors = CardDefaults.cardColors(
                        containerColor = Color.White,
                        contentColor = Color(0xFF183B35),
                        disabledContainerColor = Color(0xFFE0E0E0),
                        disabledContentColor = Color(0xFF595959)
                    )
                ) {
                    Column(
                        modifier = Modifier.padding(20.dp),
                        verticalArrangement = Arrangement.spacedBy(8.dp)
                    ) {
                        Text(
                            text = "Locker $lockerNumber",
                            style = MaterialTheme.typography.titleLarge
                        )
                        Text(
                            text = if (index < 6) "Size: Small" else "Size: Large",
                            style = MaterialTheme.typography.bodyMedium
                        )
                        Text(
                            text = if (isAvailable) "Available" else "Unavailable",
                            color = if (isAvailable) Color(0xFF237A57) else Color(0xFF595959),
                            style = MaterialTheme.typography.bodyMedium
                        )
                        Text(
                            text = if (index < 6) "€1 per day" else "€1.50 per day",
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
