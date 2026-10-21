package com.example.paybox.ui.screens

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.MaterialTheme
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
import androidx.compose.ui.unit.dp
import com.example.paybox.ui.theme.PayBoxDisabledButton
import com.example.paybox.ui.theme.PayBoxDisabledButtonText
import com.example.paybox.ui.theme.PayBoxError
import com.example.paybox.ui.theme.PayBoxErrorBackground
import com.example.paybox.ui.theme.PayBoxErrorText
import com.example.paybox.ui.theme.PayBoxGreen
import com.example.paybox.ui.theme.PayBoxInputBackground
import com.example.paybox.ui.theme.PayBoxMutedGreen
import com.example.paybox.ui.theme.PayBoxSand
import com.example.paybox.ui.theme.PayBoxSupportingText

@Composable
fun RentalScreen(
    lockerNumber: Int,
    onBack: () -> Unit,
    modifier: Modifier = Modifier
) {
    var daysInput by remember(lockerNumber) { mutableStateOf("") }
    val days = daysInput.toIntOrNull()
    val pricePerDay = if (lockerNumber <= 6) 1.0 else 1.5
    val totalPrice: Double = if (days != null && days in 1..7) days * pricePerDay else 0.0
    val isError = daysInput.isNotEmpty() && (days == null || days !in 1..7)

    Column(
        modifier = modifier
            .padding(24.dp)
            .fillMaxWidth(),
        verticalArrangement = Arrangement.spacedBy(16.dp)
    ) {
        Text(
            text = "Locker $lockerNumber",
            style = MaterialTheme.typography.headlineMedium,
            color = Color.White
        )
        TextField(
            value = daysInput,
            onValueChange = { daysInput = it },
            modifier = Modifier.fillMaxWidth(),
            label = { Text("Number of days") },
            singleLine = true,
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
            isError = isError,
            shape = RoundedCornerShape(12.dp),
            colors = TextFieldDefaults.colors(
                focusedContainerColor = PayBoxInputBackground,
                unfocusedContainerColor = PayBoxInputBackground,
                errorContainerColor = PayBoxErrorBackground,

                focusedTextColor = PayBoxGreen,
                unfocusedTextColor = PayBoxGreen,
                errorTextColor = PayBoxGreen,

                focusedLabelColor = PayBoxGreen,
                unfocusedLabelColor = PayBoxMutedGreen,
                errorLabelColor = PayBoxError,

                cursorColor = PayBoxGreen,
                errorCursorColor = PayBoxError,

                focusedIndicatorColor = Color.Transparent,
                unfocusedIndicatorColor = Color.Transparent,
                errorIndicatorColor = PayBoxError,

                focusedSupportingTextColor = PayBoxSupportingText,
                unfocusedSupportingTextColor = PayBoxSupportingText,
                errorSupportingTextColor = PayBoxErrorText
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
                containerColor = PayBoxSand,
                contentColor = PayBoxGreen,
                disabledContainerColor = PayBoxDisabledButton,
                disabledContentColor = PayBoxDisabledButtonText
            )
        ) {
            Text("Confirm rental")
        }
        Button(
            onClick = onBack,
            modifier = Modifier.fillMaxWidth(),
            colors = ButtonDefaults.buttonColors(
                contentColor = Color.White
            )
        ) {
            Text("Back")
        }
    }
}
