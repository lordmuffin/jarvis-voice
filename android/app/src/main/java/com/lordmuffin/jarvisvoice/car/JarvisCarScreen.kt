package com.lordmuffin.jarvisvoice.car

import androidx.car.app.CarContext
import androidx.car.app.Screen
import androidx.car.app.model.Action
import androidx.car.app.model.CarText
import androidx.car.app.model.MessageTemplate
import androidx.car.app.model.Template

class JarvisCarScreen(carContext: CarContext) : Screen(carContext) {

    override fun onGetTemplate(): Template {
        return MessageTemplate.Builder(CarText.create("Jarvis Voice\nAndroid Auto support coming soon."))
            .setTitle("Jarvis Voice")
            .setHeaderAction(Action.APP_ICON)
            .build()
    }
}
