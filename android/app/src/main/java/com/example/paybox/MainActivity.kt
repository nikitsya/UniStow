package com.example.paybox

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Scaffold
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import com.example.paybox.ui.screens.LockerList
import com.example.paybox.ui.screens.RentalScreen
import com.example.paybox.ui.theme.PayBoxGreen
import com.example.paybox.ui.theme.PayBoxTheme

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        setContent {
            PayBoxTheme {
                PayBoxApp()
            }
        }
    }
}

@Composable
fun PayBoxApp() {
    var selectedLocker by remember { mutableStateOf<Int?>(null) }

    Scaffold(
        modifier = Modifier.fillMaxSize(),
        containerColor = PayBoxGreen
    ) { innerPadding ->
        val lockerNumber = selectedLocker

        if (lockerNumber == null) {
            LockerList(
                modifier = Modifier.padding(innerPadding),
                onLockerClick = { selectedLocker = it }
            )
        } else {
            RentalScreen(
                lockerNumber = lockerNumber,
                onBack = { selectedLocker = null },
                modifier = Modifier.padding(innerPadding)
            )
        }
    }
}
